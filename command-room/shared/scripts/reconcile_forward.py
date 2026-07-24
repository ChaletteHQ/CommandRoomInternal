#!/usr/bin/env python3
"""
SPEC SYNC1 A3 — quarantine auto-reconcile + fail-closed merge-forward.

Invoked from `atomic_write.atomic_append_jsonl`'s events branch, INSIDE the
writer lock, immediately after acquire and BEFORE the FS-04 seq-high-water
check. It sits in FRONT of the quarantine floor — it never replaces it, and
it never adds a force-past-the-guard path.

TWO PATHS (the row-17 tightening of the handoff's R2)
-----------------------------------------------------
The trigger is cheap — a marker, a quarantine file, or a Drive conflict-copy
sibling. Absent all three this returns immediately, so a normal append pays
nothing.

  * **Healthy view** (`file_max_seq >= seqhw_max`): replay quarantined batches.
    Union by content-signature `{ts, type, source_skill, sha(data)}`;
    commitments additionally deduped by `data.id`. Events not already in the
    log get FRESH seqs `> file_max_seq` with their ORIGINAL `ts` preserved.
    Archive the quarantines + marker + `.mount_stale.json` into a
    `_recovery_<stamp>/` snapshot (never delete), sweep resolved alerts, and
    append ONE `substrate_reconciled` receipt. Idempotent — a second run finds
    nothing and writes nothing.

  * **Regressed view** (`file_max_seq < seqhw_max`): scan candidates (the live
    file, `*.bak_*`, conflict copies) for a base whose max seq REACHES the
    high-water. Found → merge-forward: snapshot everything touched, promote the
    winner, re-seq the trailing copy's stragglers above the winner's max,
    advance `.seqhw`, then replay quarantines. **No visible candidate reaches
    the high-water → REFUSE and do nothing** — the FS-04 guard downstream stays
    the fail-closed floor.

THE LOAD-BEARING RULE (`_candidate_meets_highwater`)
----------------------------------------------------
From a stale sandbox mount the "best visible copy" IS the stale one. Auto-
promoting it would make the reconciler the clobberer — the exact failure the
whole spec exists to prevent. `_candidate_meets_highwater` is the gate: promote
ONLY when a candidate's max seq reaches the recorded high-water. It is a
module-level function precisely so the regression suite can mutation-test it
(patch it to always-true and prove the reconciler becomes the clobberer on the
row-17 fixture shape, then restore). The winner may simply be invisible from
this view; only M can rule actual loss (the existing explicit M-only
seqhw-reset path, unchanged — reconcile must NEVER silently lower a high-water).
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
import sys as _sys
if str(_HERE) not in _sys.path:
    _sys.path.insert(0, str(_HERE))

from atomic_write import (  # noqa: E402
    _EPOCH_THRESHOLD,
    _read_seqhw,
    _recovery_dir,
    _regression_marker_path,
    _write_seqhw,
    atomic_write_text,
    events_freshness,
)

_local = threading.local()


# ---------------------------------------------------------------------------
# The load-bearing fail-closed rule (mutation-tested)
# ---------------------------------------------------------------------------

def _candidate_meets_highwater(candidate_max: int, seqhw: int) -> bool:
    """SPEC SYNC1 A3 — reconcile_forward may promote a candidate ONLY when its
    max seq reaches the recorded high-water. Removing this guard makes the
    reconciler promote the stale mount's copy and become the clobberer (row 17).
    Mutation-tested in run_substrate_sync_hardening_test.py."""
    return candidate_max >= seqhw


# ---------------------------------------------------------------------------
# Parsing / signatures
# ---------------------------------------------------------------------------

def _parse_jsonl(path: Path) -> list[dict]:
    """Defensive line-by-line parse — skip blank / unparseable / non-dict lines
    (same tolerance as events_io._iter_file, no alarm side effects here)."""
    out: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            out.append(ev)
    return out


def _max_seq(events: list[dict]) -> int:
    m = 0
    for ev in events:
        s = ev.get("seq")
        if (isinstance(s, (int, float)) and not isinstance(s, bool)
                and s < _EPOCH_THRESHOLD and s > m):
            m = int(s)
    return m


def _sig(ev: dict) -> tuple:
    """Content signature that IGNORES seq — {ts, type, source_skill, sha(data)}.
    Re-seqing a replayed event doesn't change its signature, so dedup is
    seq-independent (the same content in two quarantine files lands once)."""
    data = ev.get("data")
    try:
        data_blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        data_blob = repr(data)
    sha = hashlib.sha256(data_blob.encode("utf-8")).hexdigest()
    return (ev.get("ts"), ev.get("type"), ev.get("source_skill"), sha)


def _commit_id(ev: dict) -> Optional[str]:
    """The `data.id` of a `type: commitment` event (the additional commitment
    dedup key), else None."""
    if ev.get("type") != "commitment":
        return None
    data = ev.get("data")
    if isinstance(data, dict):
        i = data.get("id")
        if isinstance(i, str) and i.strip():
            return i.strip()
    return None


# ---------------------------------------------------------------------------
# Trigger detection
# ---------------------------------------------------------------------------

def _quarantine_files(events_path: Path) -> list[Path]:
    return sorted(events_path.parent.glob(events_path.name + ".quarantine-*.jsonl"))


def _mount_stale_path(events_path: Path) -> Path:
    return events_path.with_name(events_path.name + ".mount_stale.json")


def _conflict_copies(events_path: Path) -> list[Path]:
    """Drive conflict-copy siblings: `events (1).jsonl` / `events (2).jsonl` …
    and any `*.conflict`. The active file itself is never a conflict copy."""
    d = events_path.parent
    out: list[Path] = []
    stem = events_path.stem  # "events"
    for p in d.glob(f"{stem} (*).jsonl"):
        if p != events_path:
            out.append(p)
    for p in d.glob("*.conflict"):
        out.append(p)
    return sorted(set(out))


def _bak_files(events_path: Path) -> list[Path]:
    return sorted(events_path.parent.glob(events_path.name + ".bak_*"))


# ---------------------------------------------------------------------------
# Snapshot / write helpers (snapshot-never-delete)
# ---------------------------------------------------------------------------

def _snapshot(src: Path, recovery: Path) -> None:
    try:
        if src.exists():
            atomic_write_text(recovery / src.name, src.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        pass


def _archive_into(src: Path, recovery: Path) -> None:
    """Copy `src` into the recovery dir then remove it from the live path
    (snapshot-never-delete: the copy survives). mv-aside if unlink is refused."""
    _snapshot(src, recovery)
    try:
        src.unlink()
    except OSError:
        try:
            import time as _time
            src.rename(src.with_suffix(src.suffix + f".archived.{int(_time.time())}"))
        except OSError:
            pass


def _write_events(events_path: Path, events: list[dict]) -> None:
    body = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events)
    atomic_write_text(events_path, body)


def _workspace_root(events_path: Path) -> Path:
    # <root>/_hq/data/events.jsonl → root is 3 up.
    return events_path.parent.parent.parent


# ---------------------------------------------------------------------------
# Merge-forward (regressed view)
# ---------------------------------------------------------------------------

def _try_merge_forward(events_path: Path, fr: dict) -> bool:
    """Promote a candidate that reaches the high-water; re-seq stragglers above
    it; snapshot everything; advance .seqhw. Returns True on promotion, False on
    REFUSAL (no candidate reached hw — the fail-closed path)."""
    seqhw = fr.get("seqhw_max")
    if not isinstance(seqhw, int):
        return False

    live_events = _parse_jsonl(events_path)
    live_max = _max_seq(live_events)
    candidates: list[tuple[str, Path, list[dict], int]] = [
        ("__live__", events_path, live_events, live_max)
    ]
    for c in _bak_files(events_path) + _conflict_copies(events_path):
        evs = _parse_jsonl(c)
        candidates.append((c.name, c, evs, _max_seq(evs)))

    name, cpath, cevents, cmax = max(candidates, key=lambda t: t[3])

    # THE fail-closed rule. Do not move it, do not weaken it.
    if not _candidate_meets_highwater(cmax, seqhw):
        return False

    if cpath == events_path:
        # The live file already reaches the high-water (freshness lied, or a
        # concurrent write healed it) — nothing to promote.
        return True

    recovery = _recovery_dir(events_path)
    _snapshot(events_path, recovery)   # snapshot the stale live before overwrite
    _snapshot(cpath, recovery)         # snapshot the winner too

    winner_sigs = {_sig(e) for e in cevents}
    winner_ids = {cid for e in cevents if (cid := _commit_id(e))}
    merged = list(cevents)
    next_seq = cmax + 1
    for e in live_events:
        if _sig(e) in winner_sigs:
            continue
        cid = _commit_id(e)
        if cid is not None and cid in winner_ids:
            continue
        merged.append({**e, "seq": next_seq})
        winner_sigs.add(_sig(e))
        if cid:
            winner_ids.add(cid)
        next_seq += 1

    _write_events(events_path, merged)
    _write_seqhw(events_path, max(cmax, next_seq - 1))
    # The promoted-from copy is archived (snapshot already taken above).
    _archive_into(cpath, recovery)
    return True


# ---------------------------------------------------------------------------
# Quarantine replay (healthy view)
# ---------------------------------------------------------------------------

def _replay_and_receipt(events_path: Path, quarantines: list[Path], holder: str) -> None:
    live_events = _parse_jsonl(events_path)
    file_max = _max_seq(live_events)
    existing_sigs = {_sig(e) for e in live_events}
    existing_ids = {cid for e in live_events if (cid := _commit_id(e))}

    replayed: list[dict] = []
    already = 0
    next_seq = file_max + 1
    for qf in quarantines:
        for ev in _parse_jsonl(qf):
            sig = _sig(ev)
            cid = _commit_id(ev)
            if sig in existing_sigs or (cid is not None and cid in existing_ids):
                already += 1
                continue
            new_ev = {**ev, "seq": next_seq}  # fresh seq, original ts preserved
            next_seq += 1
            existing_sigs.add(sig)
            if cid:
                existing_ids.add(cid)
            replayed.append(new_ev)

    recovery = _recovery_dir(events_path)

    if replayed:
        _write_events(events_path, live_events + replayed)
        _write_seqhw(events_path, next_seq - 1)

    # Archive quarantines + marker + mount_stale sidecar (snapshot-never-delete).
    n_archived = 0
    for qf in quarantines:
        _archive_into(qf, recovery)
        n_archived += 1
    marker = _regression_marker_path(events_path)
    if marker.exists():
        _archive_into(marker, recovery)
    stale = _mount_stale_path(events_path)
    if stale.exists():
        _archive_into(stale, recovery)

    # Sweep any resolved alerts now that the view is healthy + reconciled.
    try:
        from alarm_artifacts import sweep_alerts
        sweep_alerts(_workspace_root(events_path))
    except Exception:
        pass

    # ONE substrate_reconciled receipt. Via append_event (gate-valid, seq/ts
    # auto-stamped) — the reentrancy guard makes the re-entry a no-op, and the
    # trigger artifacts are already archived, so it never recurses.
    try:
        from event_gate import append_event
        ev = {
            "type": "substrate_reconciled",
            "source_skill": "reconcile_forward",
            "data": {
                "replayed": len(replayed),
                "already_present": already,
                "archived": n_archived,
                "holder": holder,
            },
        }
        append_event(events_path, ev, holder="reconcile_forward")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def reconcile_forward(events_path, holder: str = "reconcile_forward") -> Optional[dict]:
    """See module docstring. Best-effort by contract — the caller (atomic_append)
    wraps it and the FS-04 guard is always downstream. Reentrant no-op so the
    receipt append can't recurse. Returns a small result dict when it did work,
    else None (fast no-op / refusal)."""
    if getattr(_local, "active", False):
        return None
    events_path = Path(events_path)

    quarantines = _quarantine_files(events_path)
    marker = _regression_marker_path(events_path)
    conflicts = _conflict_copies(events_path)
    mount_stale = _mount_stale_path(events_path)
    if (not quarantines and not marker.exists() and not conflicts
            and not mount_stale.exists()):
        return None  # fast no-op — the normal append path

    _local.active = True
    try:
        fr = events_freshness(events_path)
        if fr["regressed"]:
            promoted = _try_merge_forward(events_path, fr)
            if not promoted:
                # FAIL-CLOSED: no candidate reached the high-water. Do nothing;
                # the FS-04 guard in _read_stamp_write raises + quarantines.
                return {"action": "refused", "regressed": True}
            fr = events_freshness(events_path)  # recompute after promotion

        if quarantines:
            _replay_and_receipt(events_path, quarantines, holder)
            return {"action": "reconciled"}
        # A lingering `.mount_stale.json` on a now-healthy view with no batches
        # to replay (second-eyes fix): the A4 preflight refused BEFORE any write,
        # so no quarantine/marker ever existed — the sidecar was the only
        # artifact, and nothing else clears it. Archive it here (spec A4:
        # cleared at the first healthy append). No receipt for zero replays —
        # keeps the whole op idempotent.
        if mount_stale.exists():
            _archive_into(mount_stale, _recovery_dir(events_path))
        # A lingering marker on an already-healthy file with no batches to
        # replay: route it through the truth-check (self-archives a resolved
        # marker). No receipt for zero work — keeps the whole op idempotent.
        try:
            from atomic_write import check_substrate_regression
            check_substrate_regression(events_path)
        except Exception:
            pass
        return {"action": "marker-checked"}
    finally:
        _local.active = False


__all__ = ["reconcile_forward"]
