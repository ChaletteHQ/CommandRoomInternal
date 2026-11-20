"""Receipts a guard hands back when it really ran, so a closing can tell (cr1#1357).

A chat's closing used to take "this step ran" on the model's word: a key typed
into `recap_closing(..., ran=[...])` was enough, and in typed chats the two
guards (the personal-sources wall and the output check on the chat's own
prose) were reported as run when they had not run. A guard now hands back a
receipt from inside its own run, and the closing counts the guard only on a
fresh receipt for exactly that guard.

The shape is `rcpt1.<step>.<UTC stamp>.<nonce>.<check>`. The check is a short
hash over the rest, so a receipt cannot be typed from memory or copied with
the step changed; it is not a secret, and a caller set on forging one is out
of scope. A receipt is fresh for `FRESH_MINUTES` after it was issued, which is
the length of a long chat turn, so one from an earlier run of the day does not
stand in for this one.

A door call that answered ok gets a receipt of its own too (cr1#1413,
`issue_door`): the access layer stamps it on the envelope, token
`door~<module:function>`, so a merged seat's closing counts a doored step only
when the door really ran it (`door_names`).

Pure: reads and writes nothing, never raises.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Set

PREFIX = "rcpt1"
#: The guards that hand back receipts, and nothing else.
STEPS = ("chat_wall", "output_check")
#: How long a receipt stands for its run.
FRESH_MINUTES = 60
#: A receipt stamped this far ahead of the reader's clock is still accepted
#: (two processes on one turn can disagree by a little); further is refused.
SKEW_MINUTES = 5
_STAMP = "%Y%m%dT%H%M%SZ"
#: The token a door call's receipt carries in front of its `module:function`
#: (cr1#1413); dots in a dotted module are spelled as commas.
DOOR_TAG = "door~"


def _check(body: str) -> str:
    return hashlib.sha256(("cr1-turn-receipt\0" + body).encode("utf-8")).hexdigest()[:12]


def issue(step: str, now: Optional[datetime] = None) -> str:
    """A receipt for `step`, or "" for a step that is not a guard."""
    if step not in STEPS:
        return ""
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    body = ".".join((PREFIX, step, when.strftime(_STAMP), secrets.token_hex(4)))
    return body + "." + _check(body)


def issue_door(name: str, now: Optional[datetime] = None) -> str:
    """A receipt for one door call that answered ok (cr1#1413): `name` is the
    door's `module:function`. The access layer stamps it on the envelope, so
    a merged seat's closing can tell a step the door ran from one it did not.
    "" for a name that is not `module:function`."""
    if not isinstance(name, str) or name.count(":") != 1 or "~" in name:
        return ""
    token = DOOR_TAG + name.replace(".", ",")
    if "." in token or not token.strip():
        return ""
    when = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    body = ".".join((PREFIX, token, when.strftime(_STAMP), secrets.token_hex(4)))
    return body + "." + _check(body)


def _token(receipt, now: Optional[datetime] = None) -> Optional[str]:
    """The step token of a well-formed, fresh receipt, else None."""
    if not isinstance(receipt, str):
        return None
    parts = receipt.strip().split(".")
    if len(parts) != 5 or parts[0] != PREFIX or not parts[1]:
        return None
    body = ".".join(parts[:4])
    if parts[4] != _check(body):
        return None
    try:
        when = datetime.strptime(parts[2], _STAMP).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if when > current + timedelta(minutes=SKEW_MINUTES):
        return None
    if current - when > timedelta(minutes=FRESH_MINUTES):
        return None
    return parts[1]


def step_of(receipt, now: Optional[datetime] = None) -> Optional[str]:
    """The guard a well-formed, fresh receipt stands for, else None."""
    token = _token(receipt, now)
    return token if token in STEPS else None


def _each(receipts):
    if receipts is None or isinstance(receipts, (bytes, dict)):
        return []
    if isinstance(receipts, str):
        return [receipts]
    try:
        return list(receipts)
    except TypeError:
        return []


def confirmed(receipts: Optional[Iterable], now: Optional[datetime] = None) -> Set[str]:
    """Every guard that has a fresh receipt among `receipts`."""
    out: Set[str] = set()
    for rc in _each(receipts):
        step = step_of(rc, now)
        if step:
            out.add(step)
    return out


def door_names(receipts: Optional[Iterable], now: Optional[datetime] = None) -> Set[str]:
    """Every door call (`module:function`) with a fresh receipt among
    `receipts` (cr1#1413)."""
    out: Set[str] = set()
    for rc in _each(receipts):
        token = _token(rc, now)
        if token and token.startswith(DOOR_TAG):
            out.add(token[len(DOOR_TAG):].replace(",", "."))
    return out
