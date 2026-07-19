#!/usr/bin/env python3
"""Read-time corruption alarms (FS-15 — corruption must be loud).

The 2026-07-14 fullstack dogfood found Cowork's sync cache serving a TRUNCATED
entities.json for ~90 minutes while the copy on disk AND in Drive were both
clean. Every defensive reader did exactly what it was written to do — caught
the parse failure and served its fallback — and the workspace ran degraded
(default-brand documents, empty entity views) with no warning anywhere.
substrate_health's scan-time parse check (T2, FS-05/15) can't catch this
class: it reads through the same cache, and it only runs when system-health
or the brief fires — a between-fires window is invisible to it.

This module closes the gap at the READ PATH: when a defensive reader catches
a parse/IO failure on a file that EXISTS, it records the failure in a tiny
sidecar next to the file before serving its fallback. The fallback still
happens (a mid-fire crash helps nobody); what changes is that the degradation
is now ON THE RECORD, and `substrate_health.substrate_alarm_lines` — already
wired LOUD into the morning brief and system-health — surfaces it, including
after the file heals (the transient cache window is precisely the case where
the evidence would otherwise vanish before anyone looks).

Sidecar: `<file>.readalarm.json`, sibling of the failing file (the FS-04
`.seqregression.json` pattern — no workspace-root resolution needed at the
read site). Shape:

    {"file": "entities.json", "first_seen": ISO-UTC, "last_seen": ISO-UTC,
     "count": 3, "last_error": "...", "last_reader": "brand"}

A clean read does NOT clear the sidecar — clearing would erase the evidence
of a transient window before the next brief surfaces it. Sidecars age out of
the surfaced view instead (RECENT_HOURS below); the stale file itself is
harmless and tiny.

`record_read_alarm` is best-effort and NEVER raises — an alarm recorder that
can crash a read path would be a worse bug than the silence it fixes.
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# atomic_write is imported LAZILY inside record_read_alarm, never at module
# level: this module is imported by the READ paths (brand, entity_resolve,
# events_io), and a broken atomic_write.py — the live 2026-07-15 mid-update
# truncation left it a SyntaxError half-file — must degrade to "no sidecar
# written", not kill every reader at import time. Everything above this line
# must stay stdlib-only for the same reason.

# How long a recorded read failure stays in the surfaced view. 72h (not 24h)
# so a Friday-evening cache window still appears in a Monday brief.
RECENT_HOURS = 72

_SUFFIX = ".readalarm.json"


class SubstrateReadError(Exception):
    """Raised by a reader that cannot proceed past a corrupt substrate file
    (a file that EXISTS but won't read/parse). The message is plain-English
    and customer-safe — it includes the full-quit remedy — because on a
    hard-required file it may surface directly in a skill fire."""


def sidecar_path(target: str | Path) -> Path:
    """`entities.json` -> `entities.json.readalarm.json`, sibling file."""
    target = Path(target)
    return target.with_name(target.name + _SUFFIX)


def remedy_line() -> str:
    """The one remedy every FS-15 surface must carry (dogfood-verified: a
    plain window close leaves Cowork's stale sync cache live)."""
    return (
        "Fully quit and reopen Cowork (quit the app completely — closing the "
        "window is not enough) so it drops the stale sync cache."
    )


def record_read_alarm(target: str | Path, error: object, reader: str = "") -> None:
    """Record a read failure on `target` in its sidecar. Merge-updates an
    existing sidecar (first_seen kept, count incremented). Best-effort:
    NEVER raises."""
    try:
        from atomic_write import atomic_write_json
        target = Path(target)
        now = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        first_seen, count = now, 0
        prior = read_alarm_for(target)
        if prior:
            first_seen = prior.get("first_seen") or now
            if isinstance(prior.get("count"), int) and prior["count"] >= 0:
                count = prior["count"]
        atomic_write_json(sidecar_path(target), {
            "file": target.name,
            "first_seen": first_seen,
            "last_seen": now,
            "count": count + 1,
            "last_error": str(error)[:200],
            "last_reader": str(reader)[:80],
        })
    except Exception:
        pass


def read_alarm_for(target: str | Path) -> Optional[dict]:
    """The recorded alarm for `target`, or None. Defensive — a corrupt
    sidecar reads as no-alarm rather than raising."""
    try:
        data = json.loads(sidecar_path(target).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def is_recent(alarm: dict, now: Optional[_dt.datetime] = None) -> bool:
    """True when the alarm's last_seen falls inside RECENT_HOURS. A
    malformed/missing last_seen counts as recent — an alarm we can't date
    must not silently age out."""
    try:
        seen = _dt.datetime.fromisoformat(str(alarm.get("last_seen")))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=_dt.timezone.utc)
    except (ValueError, TypeError):
        return True
    now = now or _dt.datetime.now(_dt.timezone.utc)
    return (now - seen) <= _dt.timedelta(hours=RECENT_HOURS)


__all__ = [
    "RECENT_HOURS",
    "SubstrateReadError",
    "sidecar_path",
    "remedy_line",
    "record_read_alarm",
    "read_alarm_for",
    "is_recent",
]
