#!/usr/bin/env python3
"""Substrate-integrity health surfacing (T2 — FS-04 / FS-05 / FS-06 / FS-15).

One read-only report the health check + morning brief use to surface substrate
alarms in plain English. These are LOUD by design — the dogfood found the
defensive readers degrading SILENTLY (a truncated entities.json rendered
default-brand docs with no warning anywhere; a clobbered events.jsonl lost ~440
events off disk). Silence is the bug; this module is the alarm.

Checks:
  - FS-04: an events.jsonl seq-high-water regression marker left by a refused
    append (a stale lineage clobbered the live log; batch was quarantined).
  - FS-05 / FS-15: any core substrate JSON (entities / aliases / workspace_config)
    that will not parse — corruption or a mid-sync truncation.
  - FS-06: duplicate seq numbers in events.jsonl (append-gate race across
    machine forks) that mis-target seq-keyed references.
  - FS-15 (read-time): `.readalarm.json` sidecars dropped by the defensive
    readers (read_alarm.py) when a read served corrupt bytes MID-FIRE. This
    catches what the scan-time parse check cannot: the 2026-07-14 dogfood had
    Cowork's sync cache serve a truncated entities.json for ~90 minutes while
    disk and Drive were both clean — by the time any scan ran, the file read
    clean again and the evidence was gone. The sidecar survives the window;
    a recent alarm is surfaced even when the file is healthy NOW.

Pure / substrate-only / stdlib. Never raises — a check that can't run returns
a clean result, never a false alarm.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


_CORE_JSONS = ("entities.json", "aliases.json")
_CONFIG_JSONS = ("workspace_config.json",)  # under _hq/


def _events_path(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def check_seq_regression(workspace_root) -> dict | None:
    """FS-04 — the regression marker, if a clobber was caught. None = clean."""
    from atomic_write import check_substrate_regression
    return check_substrate_regression(_events_path(Path(workspace_root)))


def check_json_parse(workspace_root) -> list[dict]:
    """FS-05 / FS-15 — every core substrate JSON that will not parse. Empty =
    all clean. Each entry: {file, error}."""
    ws = Path(workspace_root)
    bad: list[dict] = []
    for name in _CORE_JSONS:
        p = ws / "_hq" / "data" / name
        if not p.exists():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            bad.append({"file": name, "error": str(e)[:120]})
    for name in _CONFIG_JSONS:
        p = ws / "_hq" / name
        if not p.exists():
            continue
        try:
            json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            bad.append({"file": name, "error": str(e)[:120]})
    return bad


def check_read_alarms(workspace_root) -> list[dict]:
    """FS-15 (read-time) — recent read-failure alarms recorded by the
    defensive readers. Empty = none. Each entry:
    {file, count, last_seen, last_reader, still_bad} where `still_bad` is
    whether the file fails to read/parse RIGHT NOW (False = the transient
    sync-cache-window case, the one a scan-only check would miss)."""
    try:
        from read_alarm import is_recent, read_alarm_for
    except ImportError:  # pragma: no cover
        return []
    ws = Path(workspace_root)
    out: list[dict] = []
    candidates: list[Path] = []
    data_dir = ws / "_hq" / "data"
    try:
        if data_dir.is_dir():
            candidates.extend(sorted(data_dir.glob("*.readalarm.json")))
    except OSError:
        pass
    cfg_sidecar = ws / "_hq" / ("workspace_config.json" + ".readalarm.json")
    if cfg_sidecar.exists():
        candidates.append(cfg_sidecar)
    for sc in candidates:
        target = sc.with_name(sc.name[: -len(".readalarm.json")])
        alarm = read_alarm_for(target)
        if not alarm or not is_recent(alarm):
            continue
        out.append({
            "file": target.name,
            "count": alarm.get("count") if isinstance(alarm.get("count"), int) else 1,
            "last_seen": str(alarm.get("last_seen") or ""),
            "last_reader": str(alarm.get("last_reader") or ""),
            "still_bad": _still_bad(target),
        })
    return out


def _still_bad(target: Path) -> bool:
    """Does `target` fail to read/parse right now? A missing file counts as
    healthy (its readers fall back quietly by contract). For .jsonl, only the
    FINAL non-blank line is checked — the truncation signature — matching
    what the readers alarm on."""
    if not target.exists():
        return False
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return True
    if target.suffix == ".jsonl":
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            return False
        try:
            json.loads(lines[-1])
            return False
        except json.JSONDecodeError:
            return True
    try:
        json.loads(text)
        return False
    except json.JSONDecodeError:
        return True


def check_duplicate_seqs(workspace_root) -> dict:
    """FS-06 — duplicate human-counter seq values in events.jsonl. Returns
    {n_duplicated, dupes: [seq, ...]}. Ignores nano-epoch artifacts (>=1e10)
    per the next_seq contract."""
    p = _events_path(Path(workspace_root))
    counts: Counter = Counter()
    if not p.exists():
        return {"n_duplicated": 0, "dupes": []}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return {"n_duplicated": 0, "dupes": []}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        s = ev.get("seq") if isinstance(ev, dict) else None
        if isinstance(s, (int, float)) and not isinstance(s, bool) and s < 10**10:
            counts[int(s)] += 1
    dupes = sorted(s for s, c in counts.items() if c > 1)
    return {"n_duplicated": len(dupes), "dupes": dupes}


def substrate_alarm_lines(workspace_root) -> list[str]:
    """The LOUD, plain-English alarm lines for the health check / brief. Empty
    list = substrate is healthy (surface nothing). Ordered most-severe first."""
    lines: list[str] = []
    reg = check_seq_regression(workspace_root)
    if reg:
        n = reg.get("n_quarantined", "some")
        lines.append(
            f"⚠ Your activity log was clobbered by an out-of-date copy — "
            f"{n} recent change(s) were set aside safely, not lost. Recover the "
            f"log from Drive version history before relying on counts. (Ask me "
            f"to walk you through it.)"
        )
    bad = check_json_parse(workspace_root)
    for b in bad:
        lines.append(
            f"⚠ I couldn't read your {b['file']} — it may be mid-sync or "
            f"corrupted. If this persists, fully quit and reopen Cowork."
        )
    # FS-15 read-time alarms. Files already reported unreadable above are
    # skipped (one loud line per file, not two). What's left is either the
    # activity log still reading truncated, or — the sync-cache-window case —
    # a file that failed DURING a fire but reads clean now: that window is
    # exactly what a scan-only check misses, so it gets its own line even
    # though everything looks healthy at scan time.
    currently_bad = {b["file"] for b in bad}
    for a in check_read_alarms(workspace_root):
        if a["file"] in currently_bad:
            continue
        when = a["last_seen"].replace("T", " ")[:16]
        when = f" (last at {when} UTC)" if when else ""
        n = a["count"]
        times = f"{n} time{'s' if n != 1 else ''}"
        if a["still_bad"]:
            lines.append(
                f"⚠ Your {a['file']} failed to read {times}{when} and still "
                f"looks cut off. Fully quit and reopen Cowork (quit the app "
                f"completely — closing the window is not enough), then check "
                f"again. Your records are very likely intact in Drive."
            )
        else:
            lines.append(
                f"⚠ Your {a['file']} failed to read {times}{when} but reads "
                f"fine now — Cowork's sync cache likely served a cut-off copy "
                f"for a while. Anything I produced in that window may have "
                f"used incomplete records, so double-check recent outputs. If "
                f"it happens again, fully quit and reopen Cowork (quit the "
                f"app completely — closing the window is not enough)."
            )
    dup = check_duplicate_seqs(workspace_root)
    if dup["n_duplicated"] > 0:
        lines.append(
            f"⚠ {dup['n_duplicated']} duplicate entry number(s) in your activity "
            f"log (from two machines writing at once) — harmless to read, but "
            f"worth a cleanup pass."
        )
    return lines


__all__ = [
    "check_seq_regression",
    "check_json_parse",
    "check_read_alarms",
    "check_duplicate_seqs",
    "substrate_alarm_lines",
]
