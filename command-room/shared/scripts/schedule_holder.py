#!/usr/bin/env python3
"""Who holds a folder's scheduled work: one computer and one account (cr1#1138).

#790 line 3. The scheduled work for a folder (Morning Brief, Maintenance, the
Hub's runs, the other tasks) is held by ONE named computer and account. A
scheduled run anywhere else is harmless: the write door refuses every write it
tries, and the one row it may leave says why (`receipts.log_fire_stopped`).

WHAT THE HOLDER WAS KEYED ON, AND WHY THAT BROKE. The declaration
(`workspace.scheduled_writer` in `entities.json`, SAFETY0) named the holder by
the writer id, `acct-<sha1(account:org:folder name)>`, and by a digest of the
folder's PATH (HYGIENE3). So:
  * a renamed folder changed both, and the holder's own fires read as foreign;
  * the same account in another organisation changed the writer id (#605's
    first point), and the owner's own seat refused itself;
  * two computers on one account with the same user path digested the same,
    and read as one computer.

WHAT IT IS KEYED ON NOW. Two marks that name neither the folder nor its path:
  * the ACCOUNT mark, `ac-<sha256("cr-account-mark|" + account uuid)[:12]>`:
    no folder name and no organisation, derived where the account uuid lives
    and forwarded as `CR_ACCOUNT_MARK` (`workspace_access.forward_env`); the
    uuid itself never crosses;
  * the COMPUTER mark, cr1#922's `computer_mark` (`CR_COMPUTER_MARK`).
A declaration carries both when the declaring seat knew them. Each half is
compared by its mark when the declaration AND the fire both carry it; where
either side lacks it, that half is compared as before (writer id; folder
digest), so an old declaration and an old fire keep today's check exactly.

A HAND-OVER TAKES EFFECT AT THE NEXT SLOT, NOT AT ONCE. `hand_over` writes the
new holder with `effective_at` = now + `HANDOVER_DELAY_SECONDS` and keeps the
old holder as `previous_*`. Until `effective_at` the old holder still holds and
the new one is refused; after it, the reverse. Two computers that both claim in
one slot both write the one declaration, the synced folder keeps one of them,
and neither starts writing before that has settled: at no time do two holders
pass (the old guard's limit 3, cr1#790).

CHECKED ON EVERY WRITE, NOT AT FIRE TIME ONLY. The write door asks
`receipts.require_writer_identity` before each dispatch, and that asks
`verdict` here with the declaration as it stands then. A run that started
before a hand-over took effect stops writing at its next write (limits 1 and 2).

READ AND COMPUTE ONLY, stdlib only, 3.10-safe. Never raises: every failure is
the fail-open answer the callers already give (a guard that cannot reach a
verdict never silences a chat, R-FIX3-2).
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from typing import Any, Dict, Optional

#: The derived account mark, forwarded on a rendered command.
ACCOUNT_MARK_ENV = "CR_ACCOUNT_MARK"
#: Where the account uuid lives (the container; a local seat's own process).
ACCOUNT_ENV = "CLAUDE_CODE_ACCOUNT_UUID"
#: cr1#922's computer mark, forwarded on a rendered command.
COMPUTER_MARK_ENV = "CR_COMPUTER_MARK"

ACCOUNT_PREFIX = "ac-"
_HEX = 12
ACCOUNT_MARK_RE = re.compile(r"^" + ACCOUNT_PREFIX + r"[0-9a-f]{%d}$" % _HEX)
_SALT = "cr-account-mark|"

#: How long a hand-over waits before it takes effect: longer than a sync of a
#: small file normally takes, shorter than the gap between two scheduled slots.
HANDOVER_DELAY_SECONDS = 600

#: The declaration keys this module adds (beside SAFETY0's and HYGIENE3's).
KEY_ACCOUNT = "account_mark"
KEY_COMPUTER = "computer_mark"
KEY_EFFECTIVE = "effective_at"
KEY_PREV_WRITER = "previous_writer_id"
KEY_PREV_ACCOUNT = "previous_account_mark"
KEY_PREV_COMPUTER = "previous_computer_mark"

#: The answers `verdict` gives, one word each (None means "this is the holder").
FOREIGN_ACCOUNT = "account"     # another account, by mark
FOREIGN_COMPUTER = "computer"   # another computer, by mark
FOREIGN_WRITER = "writer"       # another writer id (no account mark to compare)
FOREIGN_DEVICE = "device"       # another folder digest (no computer mark)


def derive_account_mark(account_uuid) -> Optional[str]:
    """The mark for one account uuid, or None for an empty one."""
    text = str(account_uuid or "").strip().lower()
    if not text or len(text) > 255 or any(c in text for c in "\n\r\x00"):
        return None
    return ACCOUNT_PREFIX + hashlib.sha256(
        (_SALT + text).encode("utf-8")).hexdigest()[:_HEX]


def is_account_mark(value) -> bool:
    return isinstance(value, str) and bool(ACCOUNT_MARK_RE.match(value.strip()))


def account_mark(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """This process's account mark: a forwarded one when well formed, else
    derived from the account uuid here, else None. A malformed forwarded
    mark is no mark (it never falls through to a derivation)."""
    try:
        e = dict(os.environ) if env is None else dict(env)
    except Exception:  # noqa: BLE001
        return None
    handed = str(e.get(ACCOUNT_MARK_ENV, "")).strip()
    if handed:
        return handed if is_account_mark(handed) else None
    return derive_account_mark(e.get(ACCOUNT_ENV))


def computer_mark_here(env: Optional[Dict[str, str]] = None) -> Optional[str]:
    """cr1#922's mark for this process, or None. Never raises."""
    try:
        import computer_mark as _cm  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import sys as _sys
            from pathlib import Path as _P
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            import computer_mark as _cm  # type: ignore
        except Exception:  # noqa: BLE001
            return None
    try:
        return _cm.computer_mark(env)
    except Exception:  # noqa: BLE001
        return None


def marks_here(env: Optional[Dict[str, str]] = None) -> Dict[str, Optional[str]]:
    """`{account, computer}` for this process."""
    return {"account": account_mark(env), "computer": computer_mark_here(env)}


def _parse(value) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        when = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when


def _now(now=None) -> _dt.datetime:
    if isinstance(now, _dt.datetime):
        return now if now.tzinfo else now.replace(tzinfo=_dt.timezone.utc)
    parsed = _parse(now) if now is not None else None
    return parsed or _dt.datetime.now(_dt.timezone.utc)


def effective_at(declared_at, delay_seconds: int = HANDOVER_DELAY_SECONDS) -> str:
    """The ISO time a hand-over declared at `declared_at` takes effect."""
    start = _now(declared_at)
    return (start + _dt.timedelta(seconds=int(delay_seconds))).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def pending(decl, now=None) -> bool:
    """True while a hand-over has not yet taken effect (the old holder holds)."""
    if not isinstance(decl, dict):
        return False
    when = _parse(decl.get(KEY_EFFECTIVE))
    return when is not None and _now(now) < when


def holder_now(decl, now=None) -> Optional[Dict[str, Any]]:
    """The fields of whoever holds the schedules at `now`, or None.

    `{writer_id, device_digest, device, account_mark, computer_mark,
    pending}`: the declaration's own holder, or, while a hand-over is
    pending, the holder it was taken from."""
    if not isinstance(decl, dict) or not decl.get("writer_id"):
        return None
    if pending(decl, now) and decl.get(KEY_PREV_WRITER):
        return {"writer_id": decl.get(KEY_PREV_WRITER),
                "device_digest": decl.get("previous_device_digest"),
                "device": decl.get("previous_device"),
                "account_mark": decl.get(KEY_PREV_ACCOUNT),
                "computer_mark": decl.get(KEY_PREV_COMPUTER),
                "pending": True}
    return {"writer_id": decl.get("writer_id"),
            "device_digest": decl.get("device_digest"),
            "device": decl.get("device"),
            "account_mark": decl.get(KEY_ACCOUNT),
            "computer_mark": decl.get(KEY_COMPUTER),
            "pending": False}


def verdict(decl, *, forwarded_writer=None, device_digest=None,
            account=None, computer=None, now=None) -> Optional[str]:
    """Why a fire is NOT the holder at `now`, or None when it is.

    The fire is described by what it was HANDED: its forwarded writer id, the
    digest of its forwarded folder path, and its two marks. Each half is
    compared by its mark when both sides carry one, else as before:
      account half: account marks, else writer ids (`writer`);
      computer half: computer marks, else folder digests (`device`, and only
      when both sides carry a digest, today's fail-open direction).
    No declaration: None (nothing is declared, nothing is foreign)."""
    held = holder_now(decl, now)
    if held is None:
        return None
    want_account = held.get("account_mark")
    if want_account and account:
        if account != want_account:
            return FOREIGN_ACCOUNT
    elif forwarded_writer != held.get("writer_id"):
        return FOREIGN_WRITER
    want_computer = held.get("computer_mark")
    if want_computer and computer:
        if computer != want_computer:
            return FOREIGN_COMPUTER
    else:
        want_digest = held.get("device_digest")
        if want_digest and device_digest and device_digest != want_digest:
            return FOREIGN_DEVICE
    return None


def fire_verdict(decl, env: Optional[Dict[str, str]] = None, now=None) -> Optional[str]:
    """`verdict` for the fire described by `env` (what the door was handed)."""
    try:
        e = dict(os.environ) if env is None else dict(env)
    except Exception:  # noqa: BLE001
        return None
    try:
        from writer_identity import forwarded_pair
        pair = forwarded_pair(e)
    except Exception:  # noqa: BLE001
        pair = None
    try:
        from schedule_config import DEVICE_WORKSPACE_ENV, device_digest
        here = device_digest(e.get(DEVICE_WORKSPACE_ENV))
    except Exception:  # noqa: BLE001
        here = None
    computer = str(e.get(COMPUTER_MARK_ENV, "")).strip() or None
    return verdict(decl, forwarded_writer=pair[0] if pair else None,
                   device_digest=here, account=account_mark(e),
                   computer=computer, now=now)


def same_holder(decl, *, account=None, computer=None) -> bool:
    """True when the declaration's CURRENT holder (pending or not) is this
    account on this computer, by marks, both known on both sides. A renamed
    folder or another organisation keeps this True."""
    if not isinstance(decl, dict):
        return False
    want_a, want_c = decl.get(KEY_ACCOUNT), decl.get(KEY_COMPUTER)
    return bool(want_a and want_c and account and computer
                and want_a == account and want_c == computer)


#: The account half of the holder sentence, said relative to the reader: the
#: account itself is never named (its id must not be printed, and no plain
#: name is known to the seat).
ACCOUNT_WORDS = {
    "this": "under this account",
    "other": "under another account",
}


def holder_account_words(decl, env: Optional[Dict[str, str]] = None, now=None) -> str:
    """`under this account` / `under another account` / `""` (unknown)."""
    held = holder_now(decl, now)
    if not held or not held.get("account_mark"):
        return ""
    here = account_mark(env)
    if not here:
        return ""
    return ACCOUNT_WORDS["this" if here == held["account_mark"] else "other"]


#: cr1#1138 part 2: the small file that names the holder, beside the
#: identity file. Written by `schedule_config` (the declaration and the
#: hand-over) and read by the bootloader BEFORE the runtime check, where no
#: helper can run yet: a plain read of a few marks. Never names the computer
#: or the folder path; never read to learn who THIS seat is (identity is
#: handed, D-1): it only says who holds.
HOLDER_FILE_REL = ("_hq", ".system", "schedule_holder.json")

#: The keys the holder file carries, and nothing else.
HOLDER_FILE_KEYS = ("writer_id", "declared_at", KEY_ACCOUNT, KEY_COMPUTER,
                    KEY_EFFECTIVE, KEY_PREV_WRITER, KEY_PREV_ACCOUNT,
                    KEY_PREV_COMPUTER)


def holder_file_doc(decl) -> Optional[Dict[str, Any]]:
    """The holder file's content for a declaration, or None (none to write)."""
    if not isinstance(decl, dict) or not decl.get("writer_id"):
        return None
    return {k: decl[k] for k in HOLDER_FILE_KEYS
            if isinstance(decl.get(k), str) and decl.get(k)}


def early_verdict(doc, env: Optional[Dict[str, str]] = None, now=None) -> Dict[str, Any]:
    """Should this fire stop BEFORE the runtime check? (cr1#1138 part 2)

    `doc` is the holder file's content (or anything, read from a synced
    folder). The fire is described by its marks only: the account mark this
    container derives (or was handed) and the computer mark of the device
    name Step 0 reported. It stops only on POSITIVE evidence: a mark known on
    both sides that differs from the holder's at `now` (a pending hand-over
    honoured). Everything else goes on to the full guard at Step 1.5, so an
    old seat, a missing or damaged file, or a fire with no marks behaves
    exactly as before.

    Returns `{stop, why, line}`; `line` is the one foreign sentence when it
    stops, else "". Never raises."""
    out: Dict[str, Any] = {"stop": False, "why": None, "line": ""}
    try:
        held = holder_now(doc, now)
        if held is None:
            return out
        e = dict(os.environ) if env is None else dict(env)
        account = account_mark(e)
        computer = _handed_computer(e)
        why = None
        if held.get("account_mark") and account and account != held["account_mark"]:
            why = FOREIGN_ACCOUNT
        elif held.get("computer_mark") and computer and computer != held["computer_mark"]:
            why = FOREIGN_COMPUTER
        if why is None:
            return out
        try:
            from schedule_config import SCHEDULED_WRITER_LINES
            line = SCHEDULED_WRITER_LINES["foreign"]
        except Exception:  # noqa: BLE001 - no sentence, no stop (fail-open)
            return out
        return {"stop": True, "why": why, "line": line}
    except Exception:  # noqa: BLE001
        return {"stop": False, "why": None, "line": ""}


def _handed_computer(env: Dict[str, str]) -> Optional[str]:
    """The computer mark this fire was HANDED: a well-formed
    `CR_COMPUTER_MARK`, else the mark of the `CR_DEVICE_NAME` Step 0
    reported. NEVER the host's own name: in a scheduled container that is the
    platform's, the same on no computer, and stopping on it would silence the
    true holder (review of part 2). No handed computer: no computer half."""
    try:
        import computer_mark as _cm  # type: ignore
    except Exception:  # noqa: BLE001
        try:
            import sys as _sys
            from pathlib import Path as _P
            _sys.path.insert(0, str(_P(__file__).resolve().parent))
            import computer_mark as _cm  # type: ignore
        except Exception:  # noqa: BLE001
            return None
    try:
        handed = str(env.get(COMPUTER_MARK_ENV, "")).strip()
        if handed:
            return handed if _cm.is_mark(handed) else None
        return _cm.derive(str(env.get(_cm.DEVICE_NAME_ENV, "")))
    except Exception:  # noqa: BLE001
        return None


def _load_doc(text):
    try:
        doc = json.loads(text) if isinstance(text, str) and text.strip() else None
    except Exception:  # noqa: BLE001
        return None
    return doc


def _main(argv=None) -> int:
    """`schedule_holder.py early (--holder '<file text>' | --holder-file PATH)`

    Prints `early_verdict`'s one JSON line. Exit 0 on everything: a failure
    is the go-on answer. Reads the environment and the one file; writes
    nothing."""
    import sys as _sys
    args = list(_sys.argv[1:] if argv is None else argv)
    answer: Dict[str, Any] = {"stop": False, "why": None, "line": ""}
    try:
        if args and args[0] == "early":
            doc = None
            if "--holder" in args:
                i = args.index("--holder")
                doc = _load_doc(args[i + 1] if i + 1 < len(args) else "")
            elif "--holder-file" in args:
                i = args.index("--holder-file")
                try:
                    with open(args[i + 1], "r", encoding="utf-8") as fh:
                        doc = _load_doc(fh.read())
                except Exception:  # noqa: BLE001
                    doc = None
            answer = early_verdict(doc)
    except Exception:  # noqa: BLE001
        pass
    print(json.dumps(answer))
    return 0


__all__ = [
    "ACCOUNT_MARK_ENV", "ACCOUNT_PREFIX", "ACCOUNT_MARK_RE", "COMPUTER_MARK_ENV",
    "HANDOVER_DELAY_SECONDS", "KEY_ACCOUNT", "KEY_COMPUTER", "KEY_EFFECTIVE",
    "KEY_PREV_WRITER", "KEY_PREV_ACCOUNT", "KEY_PREV_COMPUTER",
    "FOREIGN_ACCOUNT", "FOREIGN_COMPUTER", "FOREIGN_WRITER", "FOREIGN_DEVICE",
    "ACCOUNT_WORDS", "derive_account_mark", "is_account_mark", "account_mark",
    "computer_mark_here", "marks_here", "effective_at", "pending", "holder_now",
    "verdict", "fire_verdict", "same_holder", "holder_account_words",
    "HOLDER_FILE_REL", "HOLDER_FILE_KEYS", "holder_file_doc", "early_verdict",
]


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_main())
