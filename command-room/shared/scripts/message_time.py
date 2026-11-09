#!/usr/bin/env python3
"""One reading of a connector's message time (cr1#767).

A mail or chat tool spells a message's time its own way: ISO-8601 with `Z`
or an offset, a mail-header date (`Wed, 04 Nov 2026 09:00:00 -0800`), or a
number of seconds or milliseconds since the epoch. The book stores an
ISO-8601 instant, because every matcher compares times as instants and every
cursor compares them as strings. A raw header date written onto a commitment
could never be matched by a later reply, and one written into the sent
cursor stopped the cursor for good (every later run read 30 days).

`normalize_message_ts` is the one reading. It answers an ISO instant, or None
for a value nothing here can read or would have to guess at; the caller
refuses that message with a count and a reason on its receipt, and never
stores the raw text. Pure, never raises, no I/O. Parsed by hand rather than
by `datetime.fromisoformat`, whose accepted forms differ between the Python
versions a seat can run.
"""
from __future__ import annotations

import datetime as _dt
import re
from email.utils import parsedate_tz
from typing import Optional

#: Epoch numbers. From 1e11 up is milliseconds; a time is believed only
#: between these bounds in seconds (2001 to 2286), so a count, a year or a
#: microsecond epoch is refused rather than read as some other date.
_MS_FLOOR = 1e11
_EPOCH_MIN = 1e9
_EPOCH_MAX = 1e10

_DIGITS_RE = re.compile(r"^\d+(\.\d+)?$")

#: ISO-8601 in every shape a tool writes: extended or basic, with or without
#: a time, a zone or a fraction.
_ISO_RE = re.compile(
    r"^(\d{4})-?(\d{2})-?(\d{2})"
    r"(?:[Tt ](\d{2}):?(\d{2})(?::?(\d{2})(?:[.,](\d+))?)?"
    r"\s*([Zz]|[+-]\d{2}(?::?\d{2})?)?)?$")

#: The one spelling kept as it came: full extended form with a zone, and a
#: fraction every reader on every Python can parse. A cursor that already
#: holds this spelling keeps comparing like with like.
_KEEP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{3}|\.\d{6})?(Z|[+-]\d{2}:\d{2})$")

#: A whole RFC 2822 date: day, month, year, time and a zone, with an optional
#: weekday and a trailing comment. The header parser reads almost anything and
#: reads part of it wrong (`Nov 4, 2026 9:00 PM` came out twelve hours off),
#: so only this shape reaches it.
_HEADER_RE = re.compile(
    r"^(?:[A-Za-z]{3,9},?\s+)?\d{1,2}\s+[A-Za-z]{3,9}\.?\s+\d{2,4}\s+"
    r"\d{1,2}:\d{2}(?::\d{2})?\s+([+-]\d{4}|[A-Za-z]{1,5})(?:\s+\(.*\))?$")

#: The zone names the header parser knows. It reads any other as UTC, silently.
_HEADER_ZONES = frozenset({"UT", "UTC", "GMT", "Z", "AST", "ADT", "EST", "EDT",
                           "CST", "CDT", "MST", "MDT", "PST", "PDT"})


def _house(dt: _dt.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def normalize_message_ts(raw) -> Optional[str]:
    """An ISO instant for a message time, or None when it cannot be read.

    Accepts an aware or naive datetime, an ISO-8601 string, a whole RFC 2822
    mail-header date with a known zone, and an epoch number in seconds or
    milliseconds (as a number or a digit string). Full extended ISO with a
    time and a zone is answered as it came, so a time that was already
    readable is stored exactly as before; every other accepted form becomes
    the house spelling (`2026-11-04T17:00:00Z`), a date or a time with no
    zone read as UTC. Anything else, an empty value included, is None."""
    try:
        return _read(raw)
    except (TypeError, ValueError, IndexError, OverflowError, OSError):
        return None


def _read(raw) -> Optional[str]:
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, _dt.datetime):
        return _house(raw)
    if isinstance(raw, (int, float)):
        return _from_epoch(float(raw))
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    # Digits first: a millisecond count can also spell a basic ISO date and
    # time, and it is far likelier to be the count. Eight digits is a date.
    if _DIGITS_RE.match(text) and not (len(text) == 8 and text.isdigit()):
        return _from_epoch(float(text))
    match = _ISO_RE.match(text)
    if match:
        return _from_iso(text, match)
    return _from_header(text)


def _believed(when: _dt.datetime) -> bool:
    """Is this instant inside the bounds every form of a time is held to?"""
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return _EPOCH_MIN <= when.timestamp() < _EPOCH_MAX


def _instant(text: str, match) -> _dt.datetime:
    year, month, day, hour, minute, second, fraction, zone = match.groups()
    micro = int(((fraction or "") + "000000")[:6])
    when = _dt.datetime(int(year), int(month), int(day), int(hour or 0),
                        int(minute or 0), int(second or 0), micro)
    if zone and zone not in "Zz":
        digits = zone[1:].replace(":", "")
        delta = _dt.timedelta(hours=int(digits[:2]), minutes=int(digits[2:] or 0))
        when = when.replace(tzinfo=_dt.timezone(-delta if zone[0] == "-" else delta))
    return when


def _from_iso(text: str, match) -> Optional[str]:
    when = _instant(text, match)
    if not _believed(when):
        return None
    return text if _KEEP_RE.match(text) else _house(when)


def _from_header(text: str) -> Optional[str]:
    match = _HEADER_RE.match(text)
    if not match:
        return None
    zone = match.group(1)
    if zone[0] not in "+-" and zone.upper() not in _HEADER_ZONES:
        return None
    parts = parsedate_tz(text)
    if parts is None:
        return None
    when = _dt.datetime(*parts[:6], tzinfo=_dt.timezone(_dt.timedelta(seconds=parts[9] or 0)))
    return _house(when) if _believed(when) else None


def _from_epoch(value: float) -> Optional[str]:
    seconds = value / 1000.0 if value >= _MS_FLOOR else value
    if not _EPOCH_MIN <= seconds < _EPOCH_MAX:  # NaN fails this too
        return None
    return _house(_dt.datetime.fromtimestamp(seconds, tz=_dt.timezone.utc))


def _too_far_ahead(iso: str, now) -> bool:
    """Does `iso` lead `now` by more than the book's write gate takes? A row
    past that bound makes the gate refuse the whole batch, and the fire with
    it, so one misdated message is refused here on its own."""
    from atomic_write import TS_LEAD_MAX_SECONDS

    when = _instant(iso, _ISO_RE.match(iso))
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return (when - now).total_seconds() > TS_LEAD_MAX_SECONDS


def normalize_messages(messages, *, field: str = "ts", keep_non_dicts: bool = False,
                       now: Optional[_dt.datetime] = None):
    """`(kept, refused)`: the dicts in `messages` with `field` rewritten to
    an ISO instant, and the ones whose time could not be read, each as
    `{"message_id": ..., "ts": <the raw text>}`. A message with no time at
    all is kept as it came (an absent time is a different, safe case the
    matchers already handle); only a present, unreadable one is refused.
    Non-dict elements are dropped silently, as every leg already does, unless
    `keep_non_dicts` passes them to a caller that reports them itself. `now`,
    an aware instant, also refuses a time further ahead of it than the book
    can hold."""
    kept = []
    refused = []
    for msg in messages or []:
        if not isinstance(msg, dict):
            if keep_non_dicts:
                kept.append(msg)
            continue
        raw = msg.get(field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            kept.append(msg)
            continue
        norm = normalize_message_ts(raw)
        if norm is None or (now is not None and _too_far_ahead(norm, now)):
            refused.append({"message_id": str(msg.get("message_id") or ""),
                            "ts": str(raw)[:80]})
            continue
        if norm != raw:
            msg = dict(msg)
            msg[field] = norm
        kept.append(msg)
    return kept, refused


__all__ = ["normalize_message_ts", "normalize_messages"]
