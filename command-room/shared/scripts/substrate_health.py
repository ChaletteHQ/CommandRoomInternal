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
    # SPEC SYNC1 B1 — route through the (dormant) resolver; byte-identical to
    # `ws / "_hq" / "data" / "events.jsonl"` with no override.
    try:
        from data_root import resolve as _resolve_data_root
        return _resolve_data_root(ws) / "events.jsonl"
    except Exception:
        return ws / "_hq" / "data" / "events.jsonl"


def check_seq_regression(workspace_root) -> dict | None:
    """FS-04 — the regression marker, if a clobber was caught. None = clean.

    SPEC SYNC1 A2: check_substrate_regression now truth-checks the marker before
    returning it (a marker whose condition has resolved self-archives and
    returns None here), so a stale marker can't keep surfacing a false alarm."""
    from atomic_write import check_substrate_regression
    return check_substrate_regression(_events_path(Path(workspace_root)))


def check_stale_view(workspace_root) -> dict | None:
    """SPEC SYNC1 A1 — read-side staleness: is the events.jsonl view we can see
    behind its own recorded high-water? Returns the freshness dict when
    regressed, else None. Single-machine / fresh workspaces (no seqhw sidecar)
    return None — nothing to be stale against (back-compat, D-5)."""
    from atomic_write import events_freshness
    fr = events_freshness(_events_path(Path(workspace_root)))
    return fr if fr.get("regressed") else None


def preflight_freshness(
    workspace_root, retries: int = 3, backoff_s: float = 0.05
) -> dict:
    """SPEC SYNC1 A4 — mount-freshness preflight for scheduled fires. Run as
    step 0 of the maintenance dispatch: any write-chained fire refuses up front
    on a stale view instead of crashing into FS-04 five times and hand-writing
    an alert.

    Checks events_freshness regression + entities/aliases parse. Both stale
    mounts and mid-sync parse glitches are frequently transient (sighting #2's
    file was clean on disk), so a failing check is RETRIED ×`retries` with a
    short backoff. Still stale after the retries → write a `.mount_stale.json`
    sidecar (a sidecar, NOT an events append — any append through a stale view
    is exactly the clobber vector), render the alert via alarm_artifacts, sweep,
    and return not-ok so the caller EXITS BEFORE ANY JOB RUNS. Jobs then write no
    receipts → all stay due → auto re-fire next slot (the row-17 machinery,
    now without the five FS-04 crashes and the quarantine litter).

    Returns {ok, retries_used, detail}. A healthy workspace returns ok=True with
    retries_used=0 and never writes anything."""
    import time as _time
    from atomic_write import events_freshness

    ep = _events_path(Path(workspace_root))

    def _probe():
        fr = events_freshness(ep)
        parse_bad = check_json_parse(workspace_root)
        return fr, parse_bad

    fr, parse_bad = _probe()
    retries_used = 0
    while (fr.get("regressed") or parse_bad) and retries_used < retries:
        if backoff_s > 0:
            _time.sleep(backoff_s)
        retries_used += 1
        fr, parse_bad = _probe()

    ok = (not fr.get("regressed")) and not parse_bad
    detail = {
        "regressed": bool(fr.get("regressed")),
        "file_max_seq": fr.get("file_max_seq"),
        "seqhw_max": fr.get("seqhw_max"),
        "parse_bad": [b["file"] for b in parse_bad],
    }
    if not ok:
        _write_mount_stale_sidecar(ep, fr, retries_used)
        # Render the alert FROM a marker-shaped dict (A2: never hand-authored)
        # and immediately sweep — best-effort, must never break the preflight.
        try:
            import datetime as _dt
            from alarm_artifacts import sweep_alerts, write_alert
            marker = {
                "detected": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "file_max_seq": fr.get("file_max_seq"),
                "sidecar_max_seq": fr.get("seqhw_max"),
                "n_quarantined": 0,
                "quarantine_path": None,
                "holder": "preflight_freshness",
            }
            write_alert(workspace_root, marker)
            sweep_alerts(workspace_root)
        except Exception:
            pass
    return {"ok": ok, "retries_used": retries_used, "detail": detail}


def _write_mount_stale_sidecar(events_path, fr: dict, retries: int) -> None:
    """Best-effort `.mount_stale.json` next to events.jsonl (NOT an events
    append). reconcile_forward folds this into its receipt + clears it on the
    next healthy first-append."""
    try:
        import datetime as _dt
        from atomic_write import atomic_write_json
        from pathlib import Path as _P
        p = _P(events_path)
        gap = None
        if isinstance(fr.get("seqhw_max"), int) and isinstance(fr.get("file_max_seq"), int):
            gap = fr["seqhw_max"] - fr["file_max_seq"]
        atomic_write_json(p.with_name(p.name + ".mount_stale.json"), {
            "detected": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "file_max_seq": fr.get("file_max_seq"),
            "seqhw_max": fr.get("seqhw_max"),
            "seq_gap": gap,
            "retries": retries,
        })
    except Exception:
        pass


# SPEC SYNC1 D-4 reader half (check_entities_rev) REMOVED — M ruling 2026-07-21
# (SYNC1 review F4): it was wired to no surface and could only warn on a strict
# subset of what check_json_parse already surfaces; rev-based staleness detection
# has a structural ceiling (the sidecar syncs on the same mount as the file).
# The WRITE half stays: atomic_write stamps + bumps the `.rev` sidecar on every
# locked write — forward value if a real reader design ever lands.


def check_git_in_drive(workspace_root, max_hits: int = 25) -> list[str]:
    """SPEC SYNC1 B4 (advisory) — any `.git` directory under the workspace root
    is a git repo living inside a Drive-synced tree (sync churn + corruption
    risk). Warn + name the path; NEVER blocks. Read-only. Capped so a pathological
    tree can't hang system-health."""
    ws = Path(workspace_root)
    out: list[str] = []
    try:
        for gd in ws.rglob(".git"):
            if not gd.is_dir():
                continue
            try:
                rel = gd.parent.relative_to(ws)
            except ValueError:
                rel = gd.parent
            out.append(
                f"⚠ A git repository is living inside your synced workspace at "
                f"`{rel}` — Drive sync can corrupt a live `.git`. Relocate it to "
                f"~/repos/ (advisory; nothing is blocked)."
            )
            if len(out) >= max_hits:
                break
    except OSError:
        pass
    return out


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
    # SPEC SYNC1 A2 — sweep resolved alerts FIRST, so an alarm that has already
    # resolved can't survive even a single healthy fire (row 17: the alert
    # outlived its truth). Best-effort — a sweep failure must never blank the
    # brief's health surface.
    try:
        from alarm_artifacts import sweep_alerts
        sweep_alerts(workspace_root)
    except Exception:
        pass
    reg = check_seq_regression(workspace_root)
    if reg:
        n = reg.get("n_quarantined", "some")
        lines.append(
            f"⚠ Your activity log was clobbered by an out-of-date copy — "
            f"{n} recent change(s) were set aside safely, not lost. Recover the "
            f"log from Drive version history before relying on counts. (Ask me "
            f"to walk you through it.)"
        )
    # SPEC SYNC1 A1 — read-side staleness. Distinct from the FS-04 marker above
    # (that's a refused WRITE); this is a stale READ view with no write attempted
    # (a mid-sync flush or a stale sandbox mount). Only when no marker already
    # fired (one loud line, not two for the same condition).
    if not reg:
        stale = check_stale_view(workspace_root)
        if stale:
            gap = None
            if isinstance(stale.get("seqhw_max"), int) and isinstance(stale.get("file_max_seq"), int):
                gap = stale["seqhw_max"] - stale["file_max_seq"]
            n = gap if gap is not None else "several"
            lines.append(
                f"⚠ The activity log I can see is {n} entr"
                f"{'y' if n == 1 else 'ies'} behind its own high-water mark — "
                f"this view is stale (mid-sync, or a stale sandbox mount). Counts "
                f"and recent activity are unreliable; nothing is written while "
                f"this is true."
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
    "check_stale_view",
    "preflight_freshness",
    "check_git_in_drive",
    "check_json_parse",
    "check_read_alarms",
    "check_duplicate_seqs",
    "substrate_alarm_lines",
]
