#!/usr/bin/env python3
"""
Shard-transparent events.jsonl reads (SPEC A5).

events.jsonl grows unbounded and `atomic_append_jsonl` rewrites the whole file per
append, so write cost grows linearly with history. A5 rotates prior-calendar-year
events into immutable yearly shards (`events-2025.jsonl`) siblings of the active
`events.jsonl`, which keeps only the current year. This module is the ONE read helper
every full-history consumer routes through, so a shard split is invisible to readers.

  shard_paths(root)  -> [events-2024.jsonl, events-2025.jsonl, events.jsonl] (chronological)
  iter_events(root, since_ts=None) -> generator over all shards + active, defensive,
                                      skipping whole shards older than since_ts's year
  active_path(root)  -> the current-year file writers always append to

Tail-readers (next_seq, last-200 idempotency, recency views) keep reading ONLY the
active file — they don't use this module. Full-history readers do.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterator, Optional

try:
    from event_time import event_time
    from read_alarm import record_read_alarm
except ImportError:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_time import event_time
    from read_alarm import record_read_alarm

_SHARD_RE = re.compile(r"^events-(\d{4})\.jsonl$")


def _data_dir(root: str | Path) -> Path:
    """Resolve the `_hq/data` dir. Accepts a workspace root, the data dir, or a path
    to events.jsonl / a shard file itself."""
    root = Path(root)
    if root.name == "events.jsonl" or _SHARD_RE.match(root.name):
        return root.parent
    if (root / "events.jsonl").exists() or root.name == "data":
        return root
    return root / "_hq" / "data"


def active_path(root: str | Path) -> Path:
    return _data_dir(root) / "events.jsonl"


def _shard_year(p: Path) -> Optional[int]:
    m = _SHARD_RE.match(p.name)
    return int(m.group(1)) if m else None


def shard_paths(root: str | Path, since_ts=None) -> list[Path]:
    """Yearly shards (sorted ascending by year) then the active file, chronological.
    No-shard workspace -> [events.jsonl]. Missing active + no shards -> [].

    `since_ts` (ISO string or 'YYYY...') drops whole shards whose year is below
    the floor — a filename-only prune that never opens a too-old shard (v4.6.0
    MC2). The active file is never pruned. Callers own the correctness contract:
    prune only when every event that matters to them postdates the floor."""
    data_dir = _data_dir(root)
    floor = _floor_year(since_ts)
    shards = sorted(
        (p for p in data_dir.glob("events-*.jsonl") if _SHARD_RE.match(p.name)),
        key=lambda p: _shard_year(p) or 0,
    )
    if floor is not None:
        shards = [p for p in shards if (_shard_year(p) or floor) >= floor]
    out: list[Path] = list(shards)
    active = data_dir / "events.jsonl"
    if active.exists():
        out.append(active)
    return out


def _floor_year(since_ts) -> Optional[int]:
    if not since_ts:
        return None
    try:
        return int(str(since_ts)[:4])
    except (ValueError, TypeError):
        return None


def _iter_file(p: Path) -> Iterator[dict]:
    """Defensive line-by-line parse — skip blank / unparseable / non-dict lines.

    FS-15: two failure shapes go ON THE RECORD (a `.readalarm.json` sidecar
    the brief / system-health surface loudly) while the defensive skipping
    itself is unchanged:
      - the whole file EXISTS but won't read (OSError) — the reader would
        otherwise serve an empty history silently;
      - the FINAL non-blank line won't parse — the partial-write / truncated
        sync-cache signature. Interior junk lines stay tolerated silently
        (historical malformed lines are recovered by recover_corruption, and
        the fixtures deliberately contain some).
    """
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        if p.exists():
            record_read_alarm(p, e, reader="events_io")
        return
    lines = [ln.strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    for i, line in enumerate(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError as e:
            if i == len(lines) - 1:
                record_read_alarm(
                    p, f"final line unparseable (truncation signature): {e}",
                    reader="events_io",
                )
            continue
        # Admit only real events: a dict carrying a non-empty string `type`.
        # A structurally-valid-but-empty row ({}, {"type": null}) parses as a
        # dict but is not an event — older readers admitted it, inflating
        # load_all counts and handing type-filtering consumers a typeless row.
        # Verified 0/3221 live events lack a string type, so this drops no real
        # data. Non-dict lines ([1,2,3], "x") were already skipped.
        if isinstance(ev, dict) and isinstance(ev.get("type"), str) and ev["type"]:
            yield ev


def iter_events(root: str | Path, since_ts=None) -> Iterator[dict]:
    """Generator over every event across all shards + the active file, in chronological
    file order. `since_ts` (ISO string or 'YYYY...') skips whole shards whose year is
    below the floor — a cheap, filename-only prune that never opens a too-old shard."""
    for p in shard_paths(root, since_ts=since_ts):
        yield from _iter_file(p)


def load_all(root: str | Path, since_ts=None) -> list[dict]:
    """Materialized `iter_events` — convenience for callers that want a list."""
    return list(iter_events(root, since_ts=since_ts))


# ---- integrity invariants (SPEC A5 §4 step 5) ----

def shard_invariants(root: str | Path) -> list[str]:
    """Validate the shard set. Returns a list of human-readable violation strings
    (empty = clean). Checks: (1) every event in a shard belongs to that shard's year;
    (2) no seq appears in more than one shard/active (a crash mid-rotation can leave a
    duplicate — the cross-shard duplicate-seq invariant catches it); (3) when shards
    exist, the active file carries a `shard_rotated` marker."""
    data_dir = _data_dir(root)
    shards = [p for p in shard_paths(root) if _shard_year(p) is not None]
    violations: list[str] = []
    seen_seq: dict = {}

    for p in shard_paths(root):
        yr = _shard_year(p)
        for ev in _iter_file(p):
            s = ev.get("seq")
            if isinstance(s, int) and not isinstance(s, bool):
                if s in seen_seq and seen_seq[s] != p.name:
                    violations.append(
                        f"seq {s} duplicated across {seen_seq[s]} and {p.name}"
                    )
                else:
                    seen_seq[s] = p.name
            if yr is not None:
                ts = event_time(ev)
                ev_year = _floor_year(ts)
                if ev_year is not None and ev_year != yr:
                    violations.append(
                        f"{p.name} contains a {ev_year} event (seq {s}) — wrong shard"
                    )

    if shards:
        active = data_dir / "events.jsonl"
        has_marker = any(
            ev.get("type") == "shard_rotated" for ev in _iter_file(active)
        ) if active.exists() else False
        if not has_marker:
            violations.append(
                "shards exist but the active file has no shard_rotated marker "
                "(seq continuity at risk)"
            )
    return violations


__all__ = ["shard_paths", "active_path", "iter_events", "load_all", "shard_invariants"]


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print("shards:", [p.name for p in shard_paths(root)])
    v = shard_invariants(root)
    print("invariants:", "clean" if not v else v)
