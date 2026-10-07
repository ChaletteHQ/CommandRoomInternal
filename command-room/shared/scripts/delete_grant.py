#!/usr/bin/env python3
"""DEL1 — what a delete does when the mount refuses one, and the one ask.

WHY THIS MODULE EXISTS
----------------------
On a merged seat the customer's folder is reached from a sandbox VM, and a
delete there is refused: probe P2 (`build/P1_RESULT_2026-09-19.md`) came back
`PermissionError(1, 'Operation not permitted')` for `os.unlink`, while
`os.replace` and `os.rename` both work. So on that mount a delete is not a rare
failure to swallow — it is the STEADY state, and every delete in the plugin
has to have a second move that leaves the workspace in the shape the
surrounding operation promised.

Four delete sites in the runtime had no second move at all and would have
raised mid-operation (gap analysis §2.9 / §2.15 G7-deletion): the reconfigure
config wipe, the swept-sidecar promotion (AFTER the notes were written), the
coaching-off undo file (AFTER the profile was restored), and the migration
rollback's backup cleanup. Four others were hardened for Drive-synced mounts
years ago (`atomic_write._clear_lock_file`, `atomic_write._archive_resolved_
marker`, `alarm_artifacts`, `reconcile_forward`) and this module is their idiom
lifted into one place so a fifth site never has to reinvent it:

    try the delete; on refusal RENAME the file aside; say ONE line naming the
    path; never raise; let the surrounding operation finish.

THE ASK
-------
The device bridge can ask the customer for a per-folder delete grant
(`device_request_delete_permission`); once granted, deletes work for the rest
of that session. The ask is a consent card in the customer's face, so it is
rationed: **at most once per session, only from the weekly cleanup pass** (the
one surface whose whole job is tidying), **and only on a seat that has the
tool at all**. `may_ask` is the read half of that ration — it answers True
once per session on a merged seat and False everywhere else — and `mark_asked`
is the write half, run AFTER the tool comes back, so a seat with no such tool
can never spend the session's one ask on a call that did not happen.

Nothing here depends on the grant. The fallback above runs identically whether
the grant was given or refused; the grant only decides whether step one
succeeds. That is deliberate: a code path that behaves differently depending on
a consent card is a code path nobody can test.

3.10-safe (it runs in the sandbox VM). Never imports a connector, never calls a
tool: the ask itself is the model's call, this module only rations it.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# --------------------------------------------------------------------------
# Constants — named ONCE, here, so a grep finds every home
# --------------------------------------------------------------------------

#: The device-bridge tool that asks the customer for a delete grant. It is
#: named in exactly two places in the product: this constant and the weekly
#: cleanup skill's prose. `run_del1_test` fails the build on a third.
ASK_TOOL = "device_request_delete_permission"

#: The mv-aside tails. `stale` is the lock-release spelling (`<name>.stale.
#: <epoch>.<pid>`, the pattern the weekly stale-lock sweep already archives);
#: `archived` is the spelling the two archive-then-remove sites use, and means
#: "the content of this file reached its new home before this rename".
SUFFIX_STALE = "stale"
SUFFIX_ARCHIVED = "archived"

#: Where the once-per-session marker lives. A directory that dies WITH the
#: session — NEVER the workspace: a marker under `_hq/` would outlive the
#: session it is scoped to, would sync to the customer's other machine, and
#: would turn a consent ration into a permanent one. A machine-wide temp
#: directory is just as wrong in the other direction: one file shared by every
#: session on the machine turns "once per session" into "once, forever"
#: (fix round 1, finding H-1).
MARKER_NAME = "cr-delete-grant.json"

#: Environment variables that name a directory scoped to ONE session, most
#: specific first. `CLAUDE_CODE_TMPDIR` is the legacy Cowork sandbox's
#: `<session>/tmp` (CONTRACT.md Rule 22 line 1); `CLAUDE_TMPDIR` is the
#: sandbox VM's `$HOME/tmp` on a merged seat (breakdown A.2). Neither exists
#: in the merged CONTAINER, which is why the branches below it exist.
SESSION_TMP_VARS = ("CLAUDE_CODE_TMPDIR", "CLAUDE_TMPDIR")

#: The sandbox VM's home is `/sessions/rcw-<id>` and is wiped with the session
#: (P1 finding 3 / probe P4, `build/P1_RESULT_2026-09-19.md`). A home under
#: this prefix IS a session directory, so the marker may live in it.
VM_SESSION_HOME_PREFIX = "/sessions/"

#: The merged container is itself created and destroyed with one session
#: (breakdown A.1: `CLAUDE_CODE_REMOTE=true`, `CLAUDE_CODE_ENTRYPOINT=
#: remote_cowork`, a per-session disk allowance). On that seat the process
#: temp directory is already session-scoped and needs no discriminator in the
#: name. The second signal was spelled `ENTRYPOINT` — the shorthand in the
#: breakdown's diagram, not the variable — which made it dead (fix round 2,
#: finding L-3); `env_detect` has always used the real one.
EPHEMERAL_CONTAINER_SIGNALS = (("CLAUDE_CODE_REMOTE", "true"),
                               ("CLAUDE_CODE_ENTRYPOINT", "remote_cowork"))

#: The session id the merged container really exports — `cse_…`, the same id
#: that names its filestore mount (breakdown §2.1 line 102). Round 1 read
#: `CR_SESSION_ID`, which NOTHING in this tree or in the environment
#: produces, so the discriminator was always the process id and the ration
#: was not enforced across the two blocks of Rule 11a (fix round 2, finding
#: M-5). One variable, and it is a real one.
SESSION_ID_VAR = "CLAUDE_CODE_REMOTE_SESSION_ID"

#: The workspace root, exported by the skill preamble. Used to keep the
#: session marker OUT of the customer's folder by containment rather than by
#: the `_hq` token alone (fix round 2, finding L-6).
WORKSPACE_VAR = "CR_WORKSPACE"

#: What makes a resolved marker one SESSION's rather than one machine's.
#: `None` — nothing names this session — is the case the ask is gated on.
ANCHOR_EXPLICIT = "explicit_marker"
ANCHOR_SESSION_TMP = "session_tmp"
ANCHOR_VM_HOME = "vm_session_home"
ANCHOR_CONTAINER = "ephemeral_container"
ANCHOR_SESSION_ID = "session_id"

#: Why an ask did not happen, for the run record.
REASON_OK = "ok"
REASON_NO_TOOL = "seat_has_no_tool"
REASON_NO_SESSION_IDENTITY = "no_session_identity"
REASON_ALREADY_ASKED = "already_asked_this_session"

#: The mode that HAS the ask tool. `device_request_delete_permission` is a
#: device-bridge tool: it exists on a merged seat and nowhere else, so a
#: legacy or local seat must never be told to call it and must never spend the
#: session's single ask on it (fix round 1, finding H-2).
MERGED_MODE = "merged_cloud"

OUTCOME_GRANTED = "granted"
OUTCOME_DECLINED = "declined"
OUTCOME_NOT_ASKED = "not_asked"
OUTCOMES = (OUTCOME_GRANTED, OUTCOME_DECLINED, OUTCOME_NOT_ASKED)


# --------------------------------------------------------------------------
# The refuse-then-move fallback
# --------------------------------------------------------------------------

def _say_moved(op: str, path, exc: BaseException, dest) -> None:
    """ONE line when a refused delete became a rename. Mirrors
    `atomic_write.left_in_place`'s shape (same words up to the ending) so both
    halves of the posture read alike in a log and a stderr tail."""
    try:
        sys.stderr.write(
            f"[delete_grant] {op}: could not remove {path} "
            f"({type(exc).__name__}: {exc}); moved aside to {Path(dest).name}\n"
        )
    except Exception:
        pass


def _say_left(op: str, path, exc: BaseException) -> None:
    """ONE line when BOTH the delete and the rename were refused. Routed
    through `atomic_write.left_in_place` so the four hardened sites and these
    five say the same sentence."""
    try:
        from atomic_write import left_in_place
        left_in_place(op, path, exc)
        return
    except Exception:
        pass
    try:
        sys.stderr.write(
            f"[delete_grant] {op}: could not remove {path} "
            f"({type(exc).__name__}: {exc}); left in place\n"
        )
    except Exception:
        pass


def aside_name(path, suffix: str = SUFFIX_STALE, now: Optional[float] = None,
               pid: Optional[int] = None, aside_dir=None) -> Path:
    """The name a refused delete renames `path` to. Derivation only — never
    touches disk, so a test can pin the shape without a mount.

    `stale` carries the pid (a lock's owner matters; two writers can leave two
    asides in the same second); `archived` does not (its content already
    landed somewhere else, so the second copy is the only one). `aside_dir`
    moves the aside out of the file's own folder — used where the folder is
    one the customer looks at (the workspace root), never where the archive
    trail should stay beside the original."""
    p = Path(path)
    stamp = int(time.time() if now is None else now)
    if suffix == SUFFIX_STALE:
        tail = ".{0}.{1}.{2}".format(
            SUFFIX_STALE, stamp, os.getpid() if pid is None else pid)
    else:
        tail = ".{0}.{1}".format(suffix, stamp)
    folder = p.parent if aside_dir is None else Path(aside_dir)
    return folder / (p.name + tail)


def _free_aside(path, suffix: str, aside_dir=None) -> Path:
    """`aside_name` with the never-clobber suffix the archive sweep uses: a
    second aside in the same second gets `-2`, never overwrites the first."""
    dest = aside_name(path, suffix, aside_dir=aside_dir)
    if not dest.exists():
        return dest
    n = 2
    while True:
        candidate = dest.with_name("{0}-{1}".format(dest.name, n))
        if not candidate.exists():
            return candidate
        n += 1


def _remove_or_move_aside(path, op: str, suffix: str, aside_dir) -> Dict[str, Any]:
    p = Path(path)
    out: Dict[str, Any] = {"path": str(p), "removed": False, "moved_to": None,
                           "left_in_place": False, "reason": None}
    try:
        p.unlink()
        out["removed"] = True
        return out
    except FileNotFoundError:
        out["removed"] = True
        out["reason"] = "already gone"
        return out
    except OSError as exc:
        first: BaseException = exc

    dest = _free_aside(p, suffix, aside_dir)
    try:
        if aside_dir is not None:
            Path(aside_dir).mkdir(parents=True, exist_ok=True)
        os.rename(str(p), str(dest))
        out["moved_to"] = str(dest)
        _say_moved(op, p, first, dest)
        return out
    except OSError as exc2:
        out["left_in_place"] = True
        out["reason"] = "{0}: {1}".format(type(exc2).__name__, exc2)
        _say_left(op, p, first)
        return out


def remove_or_move_aside(path, op: str, suffix: str = SUFFIX_STALE,
                         aside_dir=None) -> Dict[str, Any]:
    """Remove `path`; on a refused delete, rename it aside instead.

    `op` is a short English name for what was being done ("reconfigure config
    wipe") — it goes into the one log line, so it is read by a person and never
    parsed. Returns a dict, always, and **never raises**: every caller of this
    function is mid-operation with something already written, and a delete that
    kills the operation after its real work landed is the exact bug this
    module exists to remove.

      {"path", "removed": bool, "moved_to": str|None,
       "left_in_place": bool, "reason": str|None}

    `removed` is True when the file is gone (deleted, or was never there).
    `moved_to` names the aside when the mount refused the delete. Both False /
    None with `left_in_place` True means the mount refused the rename too —
    the file is still live and the caller's own reader will still see it; that
    is the one case worth surfacing, and the line says so.
    """
    try:
        return _remove_or_move_aside(path, op, suffix, aside_dir)
    except Exception as exc:  # pragma: no cover — never raises, by contract
        _say_left(op, path, exc)
        return {"path": str(path), "removed": False, "moved_to": None,
                "left_in_place": True,
                "reason": "{0}: {1}".format(type(exc).__name__, exc)}


# --------------------------------------------------------------------------
# The one ask per session
# --------------------------------------------------------------------------

def _marker_is_legal(path, environ=None) -> bool:
    """A session marker may never live inside a workspace — it would sync to
    the customer's other machine and turn a per-session note into a permanent
    one. Two refusals, not one:

      * any path carrying an `_hq` component — the system folder, the marker
        of a workspace root;
      * any path CONTAINED in `$CR_WORKSPACE`. The token alone let a marker
        land at the workspace root, in a project folder or in `_archive/`,
        none of which carry `_hq` (fix round 2, finding L-6).

    Applied to EVERY candidate, not only the one the caller named: `TMPDIR` /
    `TEMP` / `TMP` steer `tempfile.gettempdir()`, so the fallback can be
    pointed inside a workspace just as easily as an explicit marker can
    (fix round 1, finding L-1). An unresolvable path is left legal — the `_hq`
    refusal above still stands and a fence that raises is worse than a fence
    that is narrow."""
    if "_hq" in Path(path).parts:
        return False
    root = ((environ if environ is not None else os.environ).get(
        WORKSPACE_VAR) or "").strip()
    if not root:
        return True
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
    except (ValueError, OSError, RuntimeError):
        return True
    return False


def _platform_temp() -> str:
    """The temp root no environment variable can move. The last resort when
    every other candidate is inside a workspace — it cannot itself carry an
    `_hq` component, so the candidate chain always terminates legally."""
    if os.name == "nt":
        return os.path.join(os.environ.get("SystemRoot") or "C:\\Windows",
                            "Temp")
    return "/tmp"


def _temp_base(environ=None) -> Path:
    """The process temp directory, or the platform root when a `TMPDIR`-style
    variable has pointed it inside a workspace."""
    base = tempfile.gettempdir()
    if not _marker_is_legal(base, environ):
        base = _platform_temp()
    return Path(base)


def _home_dir(environ) -> str:
    """The home directory as THIS process sees it, read from the passed
    environment so a fixture can put the process in the sandbox VM."""
    home = (environ.get("HOME") or environ.get("USERPROFILE") or "").strip()
    if home:
        return home
    try:
        return os.path.expanduser("~")
    except Exception:  # noqa: BLE001 — a home we cannot read is no home
        return ""


def _is_vm_session_home(home: str) -> bool:
    """`/sessions/rcw-<id>` — the sandbox VM's home, which is created and
    wiped with the session (P1 finding 3)."""
    if not home:
        return False
    posix = str(home).replace("\\", "/")
    return posix.startswith(VM_SESSION_HOME_PREFIX) and len(
        posix.rstrip("/")) > len(VM_SESSION_HOME_PREFIX)


def _is_ephemeral_container(environ) -> bool:
    """A container that lives and dies with one session: its temp directory
    is session-scoped by construction and needs no discriminator."""
    for key, value in EPHEMERAL_CONTAINER_SIGNALS:
        if (environ.get(key) or "").strip().lower() == value:
            return True
    return False


def _safe_token(value: str) -> str:
    """A filename-safe scrap of a session id — letters, digits, dash."""
    keep = [c for c in str(value) if c.isalnum() or c in "-_"]
    return "".join(keep)[:40] or "x"


def _pid_token() -> str:
    """The last resort: this PROCESS. A marker named after it is per-process,
    not per-session — two blocks of one fire would not share it — so a seat
    that gets here is a seat the ask is switched off on (`ask_gate`, finding
    M-5). It can never turn the ration into "asked once on this machine, in
    2026, forever", which is the failure it replaced."""
    return "pid{0}".format(os.getpid())


def _marker_filename(token: Optional[str] = None) -> str:
    if not token:
        return MARKER_NAME
    stem, _, ext = MARKER_NAME.rpartition(".")
    return "{0}.{1}.{2}".format(stem, token, ext)


def session_marker_path(session_marker=None, env=None) -> Path:
    """Where the once-per-session marker lives, in every environment.

    In order, first legal candidate wins:

      1. an explicit `session_marker` (a suite, or a caller with its own
         session directory);
      2. `$CLAUDE_CODE_TMPDIR` — the legacy Cowork sandbox's `<session>/tmp`;
      3. `$CLAUDE_TMPDIR` — the sandbox VM's `$HOME/tmp` on a merged seat;
      4. `~` when it resolves under `/sessions/` — the VM home, wiped with the
         session (P1 finding 3);
      5. the process temp directory with the SESSION ID in the FILENAME —
         never a bare machine-wide file, which is one marker shared by every
         session and every workspace on the machine and would silently make
         the ask once-per-machine-forever (finding H-1);
      6. otherwise the process temp directory under the plain name, when this
         process is an ephemeral per-session container (it dies with the
         session, so the plain name is already session-scoped).

    A candidate inside a workspace is refused at every step, the fallback
    included (findings L-1, L-6)."""
    return _resolve_marker(session_marker, os.environ if env is None else env)[0]


def session_anchor(session_marker=None, env=None):
    """WHAT makes this session's marker its own — one of the `ANCHOR_*`
    values, or `None` when nothing in the environment names the session and
    the marker is per-process.

    `None` is the case the ask is switched off on: the two halves of Rule 11a
    run in two separate programs, so a per-process marker means the second
    program cannot see what the first one spent, and a ration that cannot be
    enforced must not be spent at all (fix round 2, finding M-5)."""
    return _resolve_marker(session_marker, os.environ if env is None else env)[1]


def _resolve_marker(session_marker, environ):
    """(path, anchor) — the one resolution both public readers share, so the
    question "is this marker session-scoped?" can never drift from the
    question "where does it live?"."""
    if session_marker is not None and _marker_is_legal(session_marker, environ):
        return Path(session_marker), ANCHOR_EXPLICIT
    for var in SESSION_TMP_VARS:
        base = (environ.get(var) or "").strip()
        if base and _marker_is_legal(base, environ):
            return Path(base) / MARKER_NAME, ANCHOR_SESSION_TMP
    home = _home_dir(environ)
    if _is_vm_session_home(home) and _marker_is_legal(home, environ):
        return Path(home) / MARKER_NAME, ANCHOR_VM_HOME
    # The SESSION ID before the container (review F-3, the kickoff's own
    # words). Both answers are one-marker-per-session on a merged seat — the
    # container dies with the session, so its plain-named temp file is
    # session-scoped by construction — but only the id NAMES the session, and
    # the id is what the record then carries. Asking the container first also
    # put a bare `cr-delete-grant.json` in the machine-wide temp directory of
    # any box carrying the two remote signals, where a second session would
    # have found it.
    session_id = (environ.get(SESSION_ID_VAR) or "").strip()
    if session_id:
        return (_temp_base(environ) / _marker_filename(_safe_token(session_id)),
                ANCHOR_SESSION_ID)
    if _is_ephemeral_container(environ):
        return _temp_base(environ) / MARKER_NAME, ANCHOR_CONTAINER
    return _temp_base(environ) / _marker_filename(_pid_token()), None


def _read_marker(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_marker(path: Path, data: Dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def ask_is_available(env=None, mode: Optional[str] = None) -> bool:
    """Does THIS seat have the delete-permission tool at all?

    `device_request_delete_permission` is a device-bridge tool. It exists on a
    merged seat; on a legacy Cowork seat and on a Claude Code seat there is no
    such tool, so telling the model to call it is telling it to call nothing —
    silently — and any ration spent on that call is spent forever (fix round
    1, finding H-2). `CR_ENV` when the preamble exported it, `env_detect`
    otherwise; anything unreadable answers False, which costs at most one
    grant the fallback never needed."""
    if mode is None:
        environ = os.environ if env is None else env
        mode = (environ.get("CR_ENV") or "").strip()
        if not mode:
            try:
                import env_detect
                mode = env_detect.detect(env=dict(environ)).mode
            except Exception:  # noqa: BLE001 — no detector, no ask
                return False
    return mode == MERGED_MODE


def may_ask(session_marker=None, env=None, mode: Optional[str] = None) -> bool:
    """May this fire ask the customer for a delete grant? A READ — it writes
    nothing and spends nothing.

    True only on a seat that HAS the tool, and only while this session has not
    asked yet. The marker is flipped by `mark_asked`, which the caller runs
    AFTER the tool comes back, so a seat where the tool turns out not to exist
    never burns the session's one ask (finding H-2). An unreadable or absent
    marker reads as "not asked yet" — the only direction that can cost a
    customer a single extra card and the only direction that cannot silently
    disable the ask forever.

    Returning True is permission to call `ASK_TOOL` ONCE; it is not the grant,
    and nothing in this module needs the grant to be given."""
    return ask_gate(session_marker, env, mode)["may_ask"]


def ask_gate(session_marker=None, env=None,
             mode: Optional[str] = None) -> Dict[str, Any]:
    """`{"may_ask": bool, "reason": str, "anchor": str|None}` — the read
    above, plus WHY, so the run record can say it.

    Three ways to a False:

      * `seat_has_no_tool` — a legacy or local seat, where the ask tool does
        not exist (finding H-2). The ordinary answer on two of three seats.
      * `no_session_identity` — the seat HAS the tool, but nothing in its
        environment names the session, so the marker would be per-process and
        the two halves of Rule 11a (two separate programs) could not see each
        other's answer. A ration that cannot be enforced is not spent: we do
        not ask (fix round 2, finding M-5). One card not shown is cheaper
        than a card shown on every block of every fire.
      * `already_asked_this_session` — the ration, doing its job.

    A READ throughout: it writes nothing and spends nothing."""
    if not ask_is_available(env, mode):
        return {"may_ask": False, "reason": REASON_NO_TOOL, "anchor": None}
    environ = os.environ if env is None else env
    path, anchor = _resolve_marker(session_marker, environ)
    if anchor is None:
        return {"may_ask": False, "reason": REASON_NO_SESSION_IDENTITY,
                "anchor": None}
    if _read_marker(path).get("asked") is True:
        return {"may_ask": False, "reason": REASON_ALREADY_ASKED,
                "anchor": anchor}
    return {"may_ask": True, "reason": REASON_OK, "anchor": anchor}


def mark_asked(outcome: str, session_marker=None, env=None) -> Dict[str, Any]:
    """Record that the ask HAPPENED and what the customer said.

    The only writer of the marker. Called after `ASK_TOOL` returns — granted,
    declined, or nothing at all — so the ration is spent by a card the
    customer actually saw. Unknown values fold to `not_asked`: a mis-spelled
    outcome must never read as a grant."""
    if outcome not in OUTCOMES:
        outcome = OUTCOME_NOT_ASKED
    path = session_marker_path(session_marker, env)
    state = _read_marker(path)
    state["asked"] = True
    state["outcome"] = outcome
    state.setdefault("asked_at", int(time.time()))
    _write_marker(path, state)
    return grant_state(session_marker, env)


def grant_state(session_marker=None, env=None) -> Dict[str, Any]:
    """`{"asked": bool, "outcome": one of OUTCOMES}` for this session."""
    state = _read_marker(session_marker_path(session_marker, env))
    outcome = state.get("outcome")
    if outcome not in OUTCOMES:
        outcome = OUTCOME_NOT_ASKED
    return {"asked": state.get("asked") is True, "outcome": outcome}


#: The two customer sentences. Plain English, no path, no file name, no tool
#: name — they go through `validate_chat_output` like every other composed
#: line, and `run_del1_test` proves they pass it.
_NOTE_LINES = {
    OUTCOME_GRANTED: (
        "You said yes to clearing out old leftovers this week, so the ones "
        "past their keep are gone for good."
    ),
    OUTCOME_DECLINED: (
        "I asked about clearing out a few old leftovers and you said no — I "
        "moved them out of the way instead, so nothing is in your way and "
        "nothing was lost."
    ),
}


def monday_note_line(state: Optional[Dict[str, Any]] = None,
                     session_marker=None, env=None) -> Optional[str]:
    """The Monday-note sentence for this session's grant, or None.

    A line only when the customer actually answered. "I did not ask you for
    anything this week" is filler, and the house posture (COVERQUIET1) is that
    a surface with nothing to disclose says nothing — the three-valued outcome
    still goes into the run record either way, which is where an operator
    looks when they want to know whether the ask fired."""
    if state is None:
        state = grant_state(session_marker, env)
    return _NOTE_LINES.get(state.get("outcome"))


# --------------------------------------------------------------------------
# What the asides add up to (report-only)
# --------------------------------------------------------------------------

#: The two aside spellings, as glob patterns. A delete-blocked mount turns
#: every delete in the plugin into one of these, permanently, so on that seat
#: they accumulate — which is fine (nothing is lost) and is exactly the sort
#: of quiet accumulation a customer should be told about once a week rather
#: than discover on their own.
ASIDE_GLOBS = ("*.{0}.*".format(SUFFIX_STALE), "*.{0}.*".format(SUFFIX_ARCHIVED))

#: Where the census looks: the system folder only. A customer's own project
#: folders are theirs, and a sweep of them is a different (unruled) decision —
#: this is a COUNT, never a collection (fix round 1, finding M-2; M's ruling
#: was taken as the default: report, do not collect).
ASIDE_ROOT = "_hq"


def report_asides(workspace_root, root_name: str = ASIDE_ROOT) -> Dict[str, Any]:
    """Count the files a refused delete left set aside under `_hq/`.

    Report-only, by design and by name: it opens nothing, moves nothing and
    removes nothing. On a mount that allows deletes it returns zero and the
    weekly note says nothing. Never raises — a census that cannot read a
    folder reports what it could read."""
    counts = {SUFFIX_STALE: 0, SUFFIX_ARCHIVED: 0}
    base = Path(workspace_root) / root_name
    try:
        entries = sorted(base.rglob("*")) if base.is_dir() else []
    except (OSError, ValueError):
        entries = []
    for entry in entries:
        try:
            if not entry.is_file():
                continue
        except OSError:
            continue
        parts = entry.name.split(".")
        for suffix in (SUFFIX_STALE, SUFFIX_ARCHIVED):
            # `<name>.<suffix>.<epoch>[.<pid>][-N]` — the suffix is never the
            # last part, so a customer's file literally called `notes.stale`
            # is not counted.
            if suffix in parts[1:-1]:
                counts[suffix] += 1
                break
    total = counts[SUFFIX_STALE] + counts[SUFFIX_ARCHIVED]
    return {"n": total, "stale": counts[SUFFIX_STALE],
            "archived": counts[SUFFIX_ARCHIVED]}


def asides_note_line(report: Optional[Dict[str, Any]] = None,
                     workspace_root=None) -> Optional[str]:
    """The one Monday-note sentence for the asides, or None when there are
    none. Plain English, no path, no file name, no count of folders — and no
    line at all in a week where nothing was set aside (COVERQUIET1)."""
    if report is None:
        if workspace_root is None:
            return None
        report = report_asides(workspace_root)
    n = int(report.get("n") or 0)
    if n <= 0:
        return None
    if n == 1:
        return ("One file is set aside rather than deleted — nothing was "
                "removed, and nothing of yours was lost.")
    return ("{0} files are set aside rather than deleted — nothing was "
            "removed, and nothing of yours was lost.".format(n))
