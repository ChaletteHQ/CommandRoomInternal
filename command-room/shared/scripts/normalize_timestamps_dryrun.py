#!/usr/bin/env python3
"""
Historic-timestamp normalization — DRY-RUN ONLY (v4.5.2 R4 / F-15).

WHY
---
The live substrate mixes timestamp conventions on the SAME day (F-15,
disk-verified): `commitment_resolved` stamped true UTC (`18:49:05`) while the
commitment-triage `pack_run` carried the machine's naive LOCAL clock
(`11:38:03` for an ~18:38 UTC run) and the inbox pack_run `14:00:00` (local
7:00 AM). Readers treat naive stamps as UTC (`event_time.parse_ts` assigns
UTC), so every naive-LOCAL stamp reads hours wrong — out-of-order event
streams, garbage lateness windows (the F-10 P2d anomaly). Write-side is fixed
(the auto-stamp + receipt contract emit UTC); this tool addresses the RESIDUE:
history written before the migration.

WHAT THIS TOOL DOES — AND DELIBERATELY DOES NOT DO
--------------------------------------------------
REPORT ONLY. It scans every event shard read-only, classifies each event's
timestamp (aware-UTC / aware-offset / NAIVE / missing / unparseable), and for
each naive stamp prints what a normalization WOULD write: the stamp
re-interpreted in the workspace timezone (entities.json
`workspace.user_timezone`, DST-correct per event date) and converted to UTC.
Then it exits. It opens no file for writing, mutates nothing, and has no
--apply flag BY DESIGN: events.jsonl is append-only history, and M approves
any actual normalization separately (the apply tooling ships only after that
approval). If you are reading this because you want to apply the changes:
stop — that is a supervised, separately-approved operation.

CAVEAT the report states honestly: a naive stamp does not record WHICH
machine's local clock wrote it. The workspace timezone is the best available
assumption (both dogfood machines run in it); events written while traveling
would normalize wrong, which is exactly why apply is not automatic.

USAGE
    python normalize_timestamps_dryrun.py <workspace_root> [--limit N]

    --limit N   cap the per-event detail lines (default 50; summary always full)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from event_time import event_time  # noqa: E402


def _classify(raw: str):
    """Classify a timestamp string. Returns (kind, parsed_or_None) with kind in
    {aware_utc, aware_offset, naive, bare_date, unparseable}."""
    v = raw.strip()
    try:
        if v.endswith("Z"):
            dt = _dt.datetime.fromisoformat(v.replace("Z", "+00:00"))
            return "aware_utc", dt
        dt = _dt.datetime.fromisoformat(v)
    except ValueError:
        try:
            _dt.date.fromisoformat(v[:10])
            return "bare_date", None
        except ValueError:
            return "unparseable", None
    if dt.tzinfo is None:
        # A bare date parses to midnight naive — call it bare_date, not naive:
        # dates carry no clock to mis-read.
        if len(v) <= 10:
            return "bare_date", None
        return "naive", dt
    if dt.utcoffset() == _dt.timedelta(0):
        return "aware_utc", dt
    return "aware_offset", dt


def _load_tz(workspace_root):
    try:
        from tz import load_workspace_tz
        return load_workspace_tz(workspace_root), None
    except Exception as exc:  # TZResolutionError or import failure
        return _dt.timezone.utc, f"{type(exc).__name__}: {exc}"


def _iter_shard_events(workspace_root):
    """Yield (shard_name, lineno, event_dict) across every shard + the active
    file, read-only and defensive (bad lines skipped, counted)."""
    import json
    try:
        from events_io import shard_paths
        paths = shard_paths(workspace_root)
    except Exception:
        paths = [Path(workspace_root) / "_hq" / "data" / "events.jsonl"]
    bad = 0
    for p in paths:
        p = Path(p)
        if not p.exists():
            continue
        with open(p, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    bad += 1
                    continue
                if isinstance(ev, dict):
                    yield p.name, lineno, ev
    if bad:
        print(f"(skipped {bad} malformed line(s) — reported, untouched)")


def run_report(workspace_root, *, limit: int = 50) -> dict:
    """Scan + print the dry-run report. Returns the summary dict (tests use
    it). NEVER writes anything."""
    tzinfo, tz_error = _load_tz(workspace_root)
    tz_label = getattr(tzinfo, "key", None) or str(tzinfo)

    counts = {"aware_utc": 0, "aware_offset": 0, "naive": 0,
              "bare_date": 0, "unparseable": 0, "missing": 0}
    legacy_fields = {"timestamp": 0, "date": 0}
    proposals = []  # (shard, lineno, seq, type, old, proposed_utc)

    for shard, lineno, ev in _iter_shard_events(workspace_root):
        for f in legacy_fields:
            if isinstance(ev.get(f), str) and not isinstance(ev.get("ts"), str):
                legacy_fields[f] += 1
        raw = event_time(ev)
        if not raw:
            counts["missing"] += 1
            continue
        kind, dt = _classify(raw)
        counts[kind] += 1
        if kind == "naive":
            local = dt.replace(tzinfo=tzinfo)  # DST-correct per event date
            proposed = local.astimezone(_dt.timezone.utc).isoformat()
            proposals.append((shard, lineno, ev.get("seq"), ev.get("type"),
                              raw, proposed))

    print("F-15 historic-timestamp normalization — DRY RUN (nothing written)")
    print(f"workspace tz assumed for naive stamps: {tz_label}"
          + (f"  (tz resolution degraded: {tz_error})" if tz_error else ""))
    print()
    total = sum(counts.values())
    print(f"events scanned: {total}")
    for k in ("aware_utc", "aware_offset", "naive", "bare_date",
              "unparseable", "missing"):
        print(f"  {k:14s} {counts[k]}")
    if any(legacy_fields.values()):
        print(f"legacy field spellings still present (read-side handled forever): "
              f"timestamp={legacy_fields['timestamp']} date={legacy_fields['date']}")
    print()
    if not proposals:
        print("No naive timestamps found — nothing would change.")
    else:
        print(f"{len(proposals)} naive stamp(s) WOULD be normalized "
              f"(local {tz_label} -> UTC), e.g.:")
        for shard, lineno, seq, etype, old, new in proposals[:limit]:
            print(f"  {shard}:{lineno} seq={seq} type={etype}: {old} -> {new}")
        if len(proposals) > limit:
            print(f"  ... and {len(proposals) - limit} more")
    print()
    print("DRY RUN complete — no file was modified. Applying these changes is "
          "a separately-approved, supervised operation (this tool cannot do it).")
    return {"counts": counts, "legacy_fields": legacy_fields,
            "proposals": proposals, "tz": tz_label}


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("workspace_root")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    run_report(args.workspace_root, limit=args.limit)
