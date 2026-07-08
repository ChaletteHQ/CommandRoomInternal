#!/usr/bin/env python3
"""
events.jsonl yearly rotation (SPEC A5) — a cleanup-owned, threshold-gated operation.

Moves prior-calendar-year events out of the active `events.jsonl` into immutable yearly
shards (`events-<year>.jsonl`), leaving the active file with the current year only +
a `shard_rotated` MARKER as its seq-continuity anchor.

WHY THE MARKER MATTERS (load-bearing): `next_seq.py` and `atomic_append_jsonl` scan the
ACTIVE file only (by design — a multi-shard scan per append defeats the point). If a
rotation just emptied the prior years out, the active file's max seq would drop and a
fresh append could collide with an archived seq — corrupting every `supersedes_seq` /
`source_event_seq` back-reference. The marker carries `max_archived_seq` and itself takes
`max_overall + 1`, so the active file's max seq never regresses and seq stays monotonic
with ZERO changes to next_seq.py.

SAFETY: runs ONLY under the A1 `events_writer_lock` (refuses if it can't acquire it);
writes shards first, the active file last, all via `atomic_write_text`; idempotent
(a second run finds no prior-year events in the active file and no-ops); `--dry-run`
mutates nothing; finishes by rebuilding the A3 source_ref index.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from events_io import active_path, _data_dir, _iter_file, _floor_year, _SHARD_RE  # noqa: E402
from event_time import event_time  # noqa: E402

DEFAULT_THRESHOLD_BYTES = 5 * 1024 * 1024  # 5 MB
DEFAULT_THRESHOLD_LINES = 10_000


def _ev_year(ev: dict) -> Optional[int]:
    return _floor_year(event_time(ev))


def _seq(ev: dict):
    s = ev.get("seq")
    return s if isinstance(s, int) and not isinstance(s, bool) else None


def _dedup_by_seq(events: list[dict]) -> list[dict]:
    """Keep one event per seq (first wins); events with no seq are all kept. Sorted by
    seq for stable, byte-reproducible shard output."""
    seen = set()
    out = []
    for e in events:
        s = _seq(e)
        if s is not None:
            if s in seen:
                continue
            seen.add(s)
        out.append(e)
    out.sort(key=lambda e: (_seq(e) is None, _seq(e) or 0))
    return out


def _eligible(active: Path, events: list[dict], current_year: int,
              threshold_bytes: int, threshold_lines: int, force: bool) -> tuple[bool, str, dict]:
    prior = defaultdict(int)
    for e in events:
        y = _ev_year(e)
        if y is not None and y < current_year:
            prior[y] += 1
    if not prior:
        return False, "no prior-year events in the active file", dict(prior)
    if force:
        return True, "forced", dict(prior)
    size = active.stat().st_size
    lines = sum(1 for _ in active.open(encoding="utf-8", errors="replace"))
    if size > threshold_bytes or lines > threshold_lines:
        return True, f"over threshold ({size}B / {lines} lines)", dict(prior)
    return False, f"below threshold ({size}B / {lines} lines) — small file, no rotation", dict(prior)


def rotate(
    workspace_root: str | Path,
    *,
    now_iso: Optional[str] = None,
    threshold_bytes: int = DEFAULT_THRESHOLD_BYTES,
    threshold_lines: int = DEFAULT_THRESHOLD_LINES,
    dry_run: bool = False,
    force: bool = False,
) -> dict:
    """Rotate if eligible. Returns a summary dict (always includes `rotated: bool`)."""
    active = active_path(workspace_root)
    if not active.exists():
        return {"rotated": False, "reason": "no active events.jsonl"}

    events = list(_iter_file(active))  # active only — shards are already split out
    years = [_ev_year(e) for e in events if _ev_year(e) is not None]
    current_year = _floor_year(now_iso) if now_iso else (max(years) if years else None)
    if current_year is None:
        return {"rotated": False, "reason": "no datable events"}

    ok, reason, prior = _eligible(active, events, current_year,
                                  threshold_bytes, threshold_lines, force)
    if not ok:
        return {"rotated": False, "reason": reason, "would_archive": prior}
    if dry_run:
        return {"rotated": False, "dry_run": True, "would_archive": prior, "reason": reason}

    # --- the real rotation, under the A1 writer lock ---
    try:
        from writer_lock import events_writer_lock
    except Exception:
        return {"rotated": False, "reason": "A1 writer lock unavailable — refusing to rotate"}

    from atomic_write import atomic_write_text

    data_dir = _data_dir(workspace_root)
    with events_writer_lock(active, holder="rotate_events"):
        # re-read inside the lock (another writer may have appended since the eligibility check)
        events = list(_iter_file(active))
        by_year: dict[int, list] = defaultdict(list)
        current: list[dict] = []
        for e in events:
            y = _ev_year(e)
            if y is None or y >= current_year:
                current.append(e)  # current-year + undatable events stay active (conservative)
            else:
                by_year[y].append(e)

        all_seqs = [s for s in (_seq(e) for e in events) if s is not None]
        archived_seqs = [s for y in by_year for s in (_seq(e) for e in by_year[y]) if s is not None]
        max_overall = max(all_seqs) if all_seqs else 0
        max_archived = max(archived_seqs) if archived_seqs else 0
        marker = {
            "type": "shard_rotated",
            "seq": max_overall + 1,  # keeps the active file's max seq from regressing
            "ts": now_iso,
            "source_skill": "cleanup",
            "data": {
                "archived_to": [f"events-{y}.jsonl" for y in sorted(by_year)],
                "archived_count": sum(len(v) for v in by_year.values()),
                "max_archived_seq": max_archived,
            },
        }

        # shards first (merge + dedup if a shard somehow already exists — re-run safety)
        archived_summary = {}
        for y, evs in sorted(by_year.items()):
            shard = data_dir / f"events-{y}.jsonl"
            existing = list(_iter_file(shard)) if shard.exists() else []
            merged = _dedup_by_seq(existing + evs)
            atomic_write_text(shard, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in merged))
            archived_summary[y] = len(evs)

        # active file last: marker + current-year/undatable events
        new_active = [marker] + current
        atomic_write_text(active, "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in new_active))

    # rebuild the A3 dedup index over the new file set (best-effort)
    try:
        from source_ref_index import rebuild as _rebuild_idx
        _rebuild_idx(workspace_root)
    except Exception:
        pass

    return {
        "rotated": True,
        "archived": archived_summary,
        "archived_total": sum(archived_summary.values()),
        "marker_seq": marker["seq"],
        "max_archived_seq": max_archived,
        "active_remaining": len(current),
        "summary": (f"Rotated {sum(archived_summary.values())} prior-year event(s) into "
                    f"{len(archived_summary)} shard(s); active file now holds the current year."),
    }


def _main(argv: list[str]) -> int:
    if not argv:
        print("usage: rotate_events.py <workspace_root> [--dry-run] [--force] "
              "[--threshold-bytes N] [--threshold-lines N] [--now ISO]", file=sys.stderr)
        return 2
    root = argv[0]
    kw = {"dry_run": "--dry-run" in argv, "force": "--force" in argv}
    for flag, key, cast in (("--threshold-bytes", "threshold_bytes", int),
                            ("--threshold-lines", "threshold_lines", int),
                            ("--now", "now_iso", str)):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 < len(argv):
                kw[key] = cast(argv[i + 1])
    print(json.dumps(rotate(root, **kw)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
