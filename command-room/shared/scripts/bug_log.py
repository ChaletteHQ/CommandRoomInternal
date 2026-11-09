#!/usr/bin/env python3
"""The logged-bug ledger: which reports are still open, and the one way to
mark one resolved (cr1#852, split from #117).

WHY THIS EXISTS
---------------
`report-bug` writes a `bug_reported` row for every report it drafts (and a
`bug_self_fixed` row when a known pattern's fix worked), and other tooling
has written `bug_received` rows into the same ledger. Nothing ever closed
one, so every logged bug stayed open forever and no surface could say how
many were. This module is the read (`open_bugs`) and the one writer
(`resolve_bug`), through the one append path.

SHAPES
------
A bug row is any row of type `bug_reported` or `bug_received`. A
`bug_resolved` row carries `data.bug_seq` = the bug row's seq; a bug with a
resolved row pointing at it is closed. A `bug_self_fixed` row written in the
same chat right after a report closes nothing by itself (it was never
linked to a seq), so it is not read here. A bug is named to the owner by
its seq; the health check lists every open bug with that number, and that
list is where the owner reads it.

3.10-safe; stdlib + shared/scripts only. Every write goes through
`event_gate.append_event`, never a second append path.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

BUG_TYPES = frozenset({"bug_reported", "bug_received"})
RESOLVED_TYPE = "bug_resolved"
SOURCE_SKILL = "report-bug"

#: The ways an owner names a logged bug: a bare number, "bug 123", "#123",
#: "BUG-123". The number is the bug row's seq.
_REF_RE = re.compile(r"^(?:bug\s*|#|bug-)?\s*(\d{1,9})$", re.I)

LINE_RESOLVED = "Marked bug {seq} resolved. {n_open} logged bug{s} still open."
LINE_ALREADY = "Bug {seq} was already marked resolved; nothing changed."
LINE_UNKNOWN = ("I could not find a logged bug numbered {ref}; nothing changed. "
                "The health check lists the open bugs with their numbers.")
LINE_NO_REF = "Say which bug: `mark bug <number> resolved`. Nothing changed."


def parse_bug_ref(ref) -> Optional[int]:
    """The seq an owner's words name, or None."""
    m = _REF_RE.match(str(ref or "").strip())
    return int(m.group(1)) if m else None


def _events_path(workspace_root) -> Path:
    return Path(str(workspace_root)) / "_hq" / "data" / "events.jsonl"


def _events(workspace_root) -> List[Dict[str, Any]]:
    from events_io import load_events_owner_scoped

    events, _skipped = load_events_owner_scoped(workspace_root)
    return list(events)


def _bug_seq(value) -> Optional[int]:
    """A `bug_resolved` row's pointer as a number, or None. A hand-written or
    damaged row must never stop the health check from reading the ledger,
    and must never close a bug it does not name."""
    if isinstance(value, bool) or (isinstance(value, float) and not value.is_integer()):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def open_bugs(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every bug row with no `bug_resolved` row pointing at its seq, in
    ledger order. A pure function over an event list."""
    resolved = set()
    for ev in events:
        if ev.get("type") == RESOLVED_TYPE:
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            seq = _bug_seq(data.get("bug_seq"))
            if seq is not None:
                resolved.add(seq)
    return [ev for ev in events
            if ev.get("type") in BUG_TYPES and isinstance(ev.get("seq"), int)
            and ev["seq"] not in resolved]


def _bug_label(row: Dict[str, Any]) -> str:
    """The few words that tell one open bug from another, or ""."""
    data = row.get("data") if isinstance(row.get("data"), dict) else {}
    return str(data.get("title") or data.get("last_skill") or "").strip()[:80]


def open_bug_count(workspace_root) -> Dict[str, Any]:
    """`{n_open, seqs, line}` over the owner's ledger: the health check's read."""
    rows = open_bugs(_events(workspace_root))
    return {"n_open": len(rows), "seqs": [r["seq"] for r in rows],
            "line": health_line([{"seq": r["seq"], "label": _bug_label(r)}
                                 for r in rows])}


def health_line(bugs: List[Dict[str, Any]]) -> str:
    """The line the health check renders, every open bug with the number
    `mark bug <number> resolved` needs; "" when nothing is open."""
    if not bugs:
        return ""
    s = "" if len(bugs) == 1 else "s"
    listed = ", ".join(f"#{b['seq']} ({b['label']})" if b.get("label")
                       else f"#{b['seq']}" for b in bugs)
    return (f"{len(bugs)} logged bug{s} still open: {listed}. "
            "Say `mark bug <number> resolved` when one is fixed.")


def build_bug_resolved_event(bug_seq: int, *, note: str = "",
                             source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Construction only: no seq, no ts (the gate stamps those)."""
    data: Dict[str, Any] = {"bug_seq": int(bug_seq)}
    if note:
        data["note"] = str(note)[:200]
    # The literal type string, so the event-contract guard can see the writer.
    return {"type": "bug_resolved", "source_skill": source_skill, "data": data}


def resolve_bug(workspace_root, ref, *, note: str = "",
                source_skill: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Mark one logged bug resolved: ONE `bug_resolved` row through the one
    append path, or a refusal that writes nothing.

    Returns `{status, line, bug_seq?, n_open}`; `status` is `resolved`,
    `already_resolved`, `unknown` or `no_ref`."""
    seq = parse_bug_ref(ref)
    if seq is None:
        return {"status": "no_ref", "line": LINE_NO_REF}
    events = _events(workspace_root)
    rows = {ev["seq"]: ev for ev in events
            if ev.get("type") in BUG_TYPES and isinstance(ev.get("seq"), int)}
    if seq not in rows:
        return {"status": "unknown", "line": LINE_UNKNOWN.format(ref=seq)}
    still_open = {ev["seq"] for ev in open_bugs(events)}
    if seq not in still_open:
        return {"status": "already_resolved", "bug_seq": seq,
                "line": LINE_ALREADY.format(seq=seq),
                "n_open": len(still_open)}
    from event_gate import append_event

    append_event(_events_path(workspace_root),
                 [build_bug_resolved_event(seq, note=note, source_skill=source_skill)],
                 holder=source_skill)
    n_open = len(still_open) - 1
    return {"status": "resolved", "bug_seq": seq, "n_open": n_open,
            "line": LINE_RESOLVED.format(seq=seq, n_open=n_open,
                                         s="" if n_open == 1 else "s")}


__all__ = [
    "BUG_TYPES", "LINE_ALREADY", "LINE_NO_REF", "LINE_RESOLVED", "LINE_UNKNOWN",
    "RESOLVED_TYPE", "SOURCE_SKILL", "build_bug_resolved_event", "health_line",
    "open_bug_count", "open_bugs", "parse_bug_ref", "resolve_bug",
]
