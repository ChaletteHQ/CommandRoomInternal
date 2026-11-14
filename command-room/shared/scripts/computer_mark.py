#!/usr/bin/env python3
"""A lasting mark for the COMPUTER a write comes from (cr1#922, cr1#1061).

THE PROBLEM. Two ids already exist and neither names a computer:

  * `writer_identity.writer_id()` is `acct-<sha1(account[:org]:basename)>`: the
    ACCOUNT and the workspace. Two computers on one account share it (#922 saw
    two lock stamps from two computers carrying the same value).
  * `machine_identity` / `writer_lock.machine_id()` persist a token in a home
    directory, and on a merged seat that home is a fresh `/sessions/rcw-<id>`
    per session, so the token names a session.

Owner ruling 1a (2026-11-11, recorded on #1061 and #922): build a mark that
lasts across sessions on a merged seat and differs between computers even on
one account; the one-log-per-computer work keys each computer's log on it.

THE ONE FACT THAT IS BOTH LASTING AND PER COMPUTER is the computer's own name:

  * on a merged seat, the device name `get_device_info` reports. The model
    exports it as `CR_DEVICE_NAME` beside `CR_DEVICE_WORKSPACE` (the Access
    preamble, rule 1; the scheduled-chat body, point 3). `workspace_access
    .plan` derives the mark IN THE CONTAINER and forwards only the mark,
    `CR_COMPUTER_MARK`, so the raw name never rides a rendered command;
  * on a process running on the customer's own computer (desktop Code, a
    hook, a local shell under the harness), the host's own name,
    `platform.node()`. Never in the cloud container (`CLAUDE_CODE_REMOTE`),
    never in the sandbox VM (`CR_HOST_MODE=vm`, or a `/sessions/rcw-` home),
    never on the older sandbox seat (`legacy_cowork`), and never where
    `env_detect` cannot say what the seat is (`unknown`, a bare shell).

    pc-<sha256("cr-computer-mark|" + name.strip().lower())[:12]>

Normalised (NFKC, curly quotes made straight, runs of space collapsed,
lowercased, a host's domain suffix such as `.local` dropped) so the same
computer gives one mark whether the name came from the desktop app or from the
host (Windows reports `LAPTOP-AB12`, the app shows `laptop-ab12`), and a model
retyping a name does not mint a second mark. A digest so the mark can sit in a
file name and a lock stamp without spelling the name out. It is a pseudonym,
not a secret: a guessable name can be found from its mark.

WHAT IT NEVER DOES. It never reads a home-directory marker (the per-session
home is the defect) and never reads a value from the workspace (a synced file
names whichever computer wrote it last). Where no lasting fact exists -- the
older sandbox seat, whose host name is the sandbox's and the same on every
computer, or a merged seat whose chat did not export the device name -- the
answer is None, and every caller keeps today's behaviour.

THE RESIDUALS, STATED. Two computers given the same name share a mark: that is
today's one-file behaviour, never worse. A renamed computer gets a new mark:
its next log file starts beside the old one and the merged reader reads both,
so nothing is lost. Whether the desktop app's name and `platform.node()` agree
on a Mac is not verified (on Windows both are the computer name).

Stdlib only; 3.10-safe (this runs in the sandbox VM). Never raises.
"""
from __future__ import annotations

import hashlib
import os
import platform
import re
from typing import Dict, Optional

#: The derived mark, forwarded on a rendered command (`workspace_access`).
MARK_ENV = "CR_COMPUTER_MARK"
#: The raw device name the model exports from `get_device_info`. Read where the
#: rendered command is planned; never forwarded itself.
DEVICE_NAME_ENV = "CR_DEVICE_NAME"

PREFIX = "pc-"
_HEX = 12
MARK_RE = re.compile(r"^" + PREFIX + r"[0-9a-f]{%d}$" % _HEX)
_SALT = "cr-computer-mark|"

#: The modes in which a process that is neither the cloud container nor the
#: sandbox VM runs on the customer's own computer, so the host name is a real
#: computer's name. `merged_cloud` is here because desktop Code's own shell
#: reads as merged (signal S10: an account, no plugin root) while running on
#: the computer itself; the container and the VM are excluded by fact below.
LOCAL_HOST_MODES = ("claude_code_local", "merged_cloud")

_VM_HOME_PREFIX = "/sessions/rcw-"
_QUOTES = {"‘": "'", "’": "'", "“": '"', "”": '"'}


def _normalise(device_name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKC", device_name)
    for curly, straight in _QUOTES.items():
        name = name.replace(curly, straight)
    name = " ".join(name.split()).lower()
    head = name.split(".", 1)[0].strip()
    return head or name


def derive(device_name) -> Optional[str]:
    """The mark for one computer name, or None for an empty or unusable name."""
    if not isinstance(device_name, str):
        return None
    name = _normalise(device_name)
    if not name or len(name) > 255 or any(c in name for c in "\n\r\x00"):
        return None
    digest = hashlib.sha256((_SALT + name).encode("utf-8")).hexdigest()
    return PREFIX + digest[:_HEX]


def is_mark(value) -> bool:
    return isinstance(value, str) and bool(MARK_RE.match(value.strip()))


def _mode(env: Dict[str, str]) -> Optional[str]:
    try:
        import env_detect  # type: ignore
    except ImportError:
        try:
            import sys as _sys
            from pathlib import Path as _P
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            import env_detect  # type: ignore
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        mode = getattr(env_detect.detect(env=env), "mode", None)
    except Exception:  # noqa: BLE001
        return None
    return mode if isinstance(mode, str) else None


def _on_this_computer(env: Dict[str, str]) -> bool:
    """False in the cloud container and in the sandbox VM, by fact: those
    hosts' names are the platform's, the same on every computer."""
    if str(env.get("CLAUDE_CODE_REMOTE", "")).strip().lower() in ("1", "true", "yes"):
        return False
    if str(env.get("CR_HOST_MODE", "")).strip().lower() == "vm":
        return False
    home = str(env.get("HOME") or env.get("USERPROFILE") or "").replace("\\", "/")
    if home.startswith(_VM_HOME_PREFIX):
        return False
    return True


def computer_mark(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """This computer's mark, or None when nothing lasting names it.

    Order: a forwarded mark (merged seat, derived in the container); a device
    name in this process's environment; the host's own name on a seat whose
    files are local. Anything else is None. Never raises."""
    try:
        e = dict(os.environ) if env is None else dict(env)
    except Exception:  # noqa: BLE001
        return None
    forwarded = str(e.get(MARK_ENV, "")).strip()
    if forwarded:
        return forwarded if is_mark(forwarded) else None
    named = derive(str(e.get(DEVICE_NAME_ENV, "")))
    if named:
        return named
    if _on_this_computer(e) and _mode(e) in LOCAL_HOST_MODES:
        try:
            return derive(platform.node())
        except Exception:  # noqa: BLE001
            return None
    return None


__all__ = ["MARK_ENV", "DEVICE_NAME_ENV", "PREFIX", "MARK_RE",
           "LOCAL_HOST_MODES", "derive", "is_mark", "computer_mark"]


if __name__ == "__main__":
    print(computer_mark() or "")
