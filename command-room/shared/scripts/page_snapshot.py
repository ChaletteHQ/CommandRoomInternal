#!/usr/bin/env python3
"""Page-set snapshots for paginated surfaces (PAGESNAP).

WHY THIS EXISTS
---------------

`surface_drivers.run_surface` rebuilt the data view FROM LIVE SUBSTRATE on
every call, including page 2. Page 2 was therefore a *different query result*
than page 1, sliced by an index computed against page 1's ordering. Any write
landing between the two renders shifted every row after it:

  - insert ahead of the slice point -> the tail of page 1 reappears at the top
    of page 2 (duplicates; the duplicate count equals the insert count);
  - delete ahead of the slice point -> the rows that should have opened page 2
    are skipped entirely and appear on NO page. Silent. This is the one that
    loses user-visible work.

Observed live on 2026-07-28 (Staff Meeting): header "18 waiting on you" on
page 1, "22" on page 2; page-2 rows 16/17 were verbatim page-1 rows 14/15;
two reopened identity proposals were counted in the chip and rendered on
neither page.

THE MODEL
---------

A page-set is the answer to ONE question asked at ONE moment. Page 1 freezes
the built view to disk; every later page of that fire slices the FROZEN view.
Nothing is re-read, so nothing shifts.

  page 1  -> build live -> save_pageset() -> slice -> render
  page N  -> load_pageset() -> slice the SAME list -> render

WHERE IT LIVES (ruling)
-----------------------

On disk, at `_hq/.system/widgets/pagesets/<surface>.json`.

`show more` re-fires the driver as a FRESH OS PROCESS (the skills invoke
`python3 shared/scripts/surface_drivers.py <surface> --page N+1`), so an
in-process cache cannot survive between pages — this is a mechanism
constraint, not a preference. Serializing the view through the transport and
back would push the entire unbounded view through the agent turn, which is
the exact payload cost paginate-by-design exists to avoid. Disk it is, next
to the persisted widget HTML that is already written there, in a `pagesets/`
subdirectory so the JSON stays out of the `*.html` audit glob.

Keyed by SURFACE: one live page-set per surface, overwritten by each page-1
fire. Four files maximum, no growth, nothing to sweep.

STALENESS (ruling)
------------------

Snapshot-authoritative within `DEFAULT_TTL_MINUTES`, and an EXPLICIT refresh
otherwise. Never a silent re-read — the silent re-read IS the bug.

Inside the window the snapshot is served as-is: that is what makes "no row on
two pages / no row skipped / stable total" true. Outside it (or on a missing,
corrupt, or mismatched snapshot) the caller rebuilds, starts a new page-set,
and stamps `pagination["refreshed"]` + `refresh_reason` + `previous_total` so
the surface can SAY the list refreshed rather than quietly serving a
differently-shaped answer.

Deliberately NOT done: reading live substrate on pages 2+ to report "N new
since you started". That would render a count from a different read than its
rows — the precise failure class this module exists to remove. New rows
arrive on the next fire, which is the honest boundary of a page-set.

APPLY-CHOICES (ruling)
----------------------

The snapshot SURVIVES an Apply. Invalidating it would force a rebuild on the
single most common path (apply on page 1, then `show more`), reintroducing
the skip on exactly the flow the user is most engaged in.

Instead, rows the user has already applied in this page-set are suppressed
AFTER the slice (`suppress_applied`). Slicing against the frozen list keeps
every index stable; dropping a row post-slice shortens that one page rather
than pulling rows across a boundary. Net behavior: the next page reflects the
Apply (handled rows do not come back) and nothing else moves.

The applied set is DERIVED from the substrate's own `apply_choices_applied`
audit events written after the snapshot, so no caller has to remember to
register anything — a skill that forgets a bookkeeping call is a skill that
silently reintroduces the bug.

stdlib only.
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Optional

# The page-set format. Bump when the on-disk shape changes so a stale file
# from an older plugin build is treated as a miss rather than misread.
PAGESET_VERSION = 1

# How long a page-set stays authoritative. Long enough to outlive a realistic
# pause (read page 1, deal with something, come back and say `show more`);
# short enough that a morning page-set never answers an afternoon `show more`.
DEFAULT_TTL_MINUTES = 30

# Outcomes on an `apply_choices_applied` row that mean the write LANDED. A
# refusal must not suppress the row — the user still has to deal with it.
# ("already_resolved" counts: the row is closed either way.)
_APPLIED_OUTCOMES = frozenset({"ok", "already_resolved"})

_MISS_NO_FILE = "no_snapshot"
_MISS_UNREADABLE = "unreadable_snapshot"
_MISS_VERSION = "version_mismatch"
_MISS_SURFACE = "surface_mismatch"
_MISS_EXPIRED = "expired"


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_ts(value) -> Optional[_dt.datetime]:
    if not value:
        return None
    try:
        txt = str(value).replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(txt)
    except (TypeError, ValueError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_dt.timezone.utc)
    return dt


def _iso(dt: _dt.datetime) -> str:
    return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def pageset_dir(workspace_root) -> Path:
    """The page-set home — beside the persisted widget HTML, in its own
    subdirectory so it never lands in the `*.html` audit glob."""
    return Path(workspace_root) / "_hq" / ".system" / "widgets" / "pagesets"


def pageset_path(workspace_root, surface: str) -> Path:
    safe = "".join(c if (c.isalnum() or c in "._-") else "-" for c in surface)
    return pageset_dir(workspace_root) / f"{safe or 'surface'}.json"


def count_items(view: dict) -> int:
    """Top-level rows in a data view — the unit pagination slices."""
    return sum(len(sec.get("items") or [])
               for sec in (view.get("sections") or []))


def save_pageset(workspace_root, surface: str, view: dict, *,
                 now_iso: str | None = None) -> dict:
    """Freeze `view` as the page-set for `surface`. Called on page 1 only.

    Returns the metadata block (also embedded in the file). A failure to write
    is NOT fatal: the surface still renders page 1 correctly, and page 2 will
    read the miss as `no_snapshot` and refresh loudly. Losing the snapshot
    must never cost the user their page 1.
    """
    from atomic_write import atomic_write_json

    created = now_iso or _iso(_now())
    meta = {
        "version": PAGESET_VERSION,
        "surface": surface,
        "created_at": created,
        "total_items": count_items(view),
    }
    payload = dict(meta)
    payload["view"] = view
    try:
        atomic_write_json(pageset_path(workspace_root, surface), payload,
                          indent=None)
    except Exception as exc:  # pragma: no cover - defensive
        return dict(meta, saved=False, error=str(exc))
    return dict(meta, saved=True)


def load_pageset(workspace_root, surface: str, *,
                 ttl_minutes: int = DEFAULT_TTL_MINUTES,
                 now_iso: str | None = None) -> tuple[Optional[dict], dict]:
    """Return `(view, meta)` for the live page-set, or `(None, meta)` on a
    miss. `meta["reason"]` names the miss so the caller can say WHY it
    refreshed instead of refreshing silently.
    """
    path = pageset_path(workspace_root, surface)
    if not path.exists():
        return None, {"reason": _MISS_NO_FILE}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, {"reason": _MISS_UNREADABLE}
    if not isinstance(payload, dict):
        return None, {"reason": _MISS_UNREADABLE}
    if payload.get("version") != PAGESET_VERSION:
        return None, {"reason": _MISS_VERSION,
                      "previous_total": payload.get("total_items")}
    if payload.get("surface") != surface:
        return None, {"reason": _MISS_SURFACE}
    view = payload.get("view")
    if not isinstance(view, dict):
        return None, {"reason": _MISS_UNREADABLE}

    created = _parse_ts(payload.get("created_at"))
    now = _parse_ts(now_iso) or _now()
    meta = {"created_at": payload.get("created_at"),
            "total_items": payload.get("total_items")}
    if created is None:
        return None, dict(meta, reason=_MISS_UNREADABLE)
    age = now - created
    if age > _dt.timedelta(minutes=max(0, int(ttl_minutes))):
        return None, dict(meta, reason=_MISS_EXPIRED,
                          previous_total=payload.get("total_items"),
                          age_minutes=round(age.total_seconds() / 60.0, 1))
    return view, dict(meta, reason="fresh",
                      age_minutes=round(age.total_seconds() / 60.0, 1))


def applied_ids_since(workspace_root, since_iso: str | None) -> set:
    """Wire ids (`n`) the user has successfully applied since `since_iso`,
    read from the substrate's own `apply_choices_applied` audit events.

    Derived rather than registered on purpose: a bookkeeping call a skill can
    forget is a silent regression waiting to happen. Never raises — a
    suppression set that cannot be computed is empty, which degrades to
    "shows a row you already handled" (visible, harmless) rather than to
    "hides a row you still owe" (silent, the bug).
    """
    if not since_iso:
        return set()
    floor = _parse_ts(since_iso)
    if floor is None:
        return set()
    out: set = set()
    try:
        from events_io import iter_events
        for ev in iter_events(workspace_root):
            if ev.get("type") != "apply_choices_applied":
                continue
            ts = _parse_ts(ev.get("ts"))
            if ts is None or ts < floor:
                continue
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            for row in data.get("actions") or []:
                if not isinstance(row, dict):
                    continue
                if str(row.get("outcome") or "") in _APPLIED_OUTCOMES:
                    n = row.get("n")
                    if n not in (None, ""):
                        out.add(str(n))
    except Exception:  # pragma: no cover - defensive
        return set()
    return out


def suppress_applied(view: dict, applied: set) -> tuple[dict, int]:
    """Drop rows whose wire id is in `applied` from an ALREADY-SLICED view.

    Order matters and is the whole point: slicing happens against the frozen
    list so indexes never move; suppression happens after, so removing a row
    shortens this one page instead of pulling the next row across the page
    boundary. Sections left empty are dropped. `pagination` is preserved
    untouched — the reported total stays the page-set's total, which is what
    keeps the header stable across the fire.
    """
    if not applied:
        return view, 0
    removed = 0
    new_sections = []
    for section in view.get("sections") or []:
        kept = []
        for item in section.get("items") or []:
            if str(item.get("n")) in applied:
                removed += 1
                continue
            kept.append(item)
        if kept:
            sec = dict(section)
            sec["items"] = kept
            new_sections.append(sec)
    if not removed:
        return view, 0
    out = dict(view)
    out["sections"] = new_sections
    return out, removed


__all__ = [
    "DEFAULT_TTL_MINUTES",
    "PAGESET_VERSION",
    "applied_ids_since",
    "count_items",
    "load_pageset",
    "pageset_dir",
    "pageset_path",
    "save_pageset",
    "suppress_applied",
]
