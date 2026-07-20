#!/usr/bin/env python3
"""Write-side cleanup remediations (v3.19.x — SPEC CLEAN1).

`integrity_check.py` is strictly READ-ONLY (it detects). This module holds the
bounded, client-safe REMEDIATE actions the weekly `cleanup` skill is allowed to
take, plus the read-only staleness flags it surfaces in the Monday note.

CLIENT SAFETY (mandatory — this runs on 5 live client workspaces with hand-made
folders and real data):
  - Never deletes a user's folders or files. Orphan folders are FLAGGED by
    `integrity_check.scan_project_structure`, never removed here.
  - Never rewrites `entities.json` / `events.jsonl` (the substrate). Only derived
    views regenerate, and only when their content actually changed.
  - Never overwrites an existing SESSION_NOTES file — `backfill_session_notes`
    scaffolds only when none exists.
  - ARCHIVE-ONLY policy (Command Room build, 2026-06): nothing is ever deleted —
    not even machine cruft. The lock sweep MOVES `*.lock.stale.*` sentinels older
    than 1 hour (only inside `_hq/data/` and `_hq/.system/`) into
    `_archive/stale-locks/`, mirroring their original path. A non-technical CEO is
    never surprised by a vanished file; everything that leaves its working spot
    lands in `_archive/` instead. ONE ruled exception (LB2 D5, M 2026-07-19):
    `*.readalarm.json` sidecars >30 days past their last recorded failure are
    DELETED by `prune_stale_readalarms` — derived alarm state, never substrate
    or user files; see the function's comment block.

Every function is idempotent: a re-run on an already-clean workspace makes zero
writes (acceptance #7).

Public API
----------
  sweep_stale_locks(root, max_age_s=3600, now=None) -> list[str]   # archive-moves, never deletes
  prune_stale_readalarms(root, max_age_days=30, now=None) -> list[str]  # the ONE delete exception (LB2 D5 — derived alarm state, see the function)
  backfill_session_notes(root, folder_name, project_display=None, today=None) -> str | None
  regenerate_decision_log_if_changed(root) -> dict
  check_analytical_view_staleness(root, now=None) -> list[dict]
  insight_generator_staleness(root, now=None, threshold_days=14) -> dict
  check_aliases_staleness(root) -> dict | None
"""
from __future__ import annotations

import datetime
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
import sys

sys.path.insert(0, str(ROOT))

from atomic_write import atomic_write_text  # noqa: E402
from event_time import event_time  # noqa: E402
import render_decision_log  # noqa: E402
import render_master_tracker  # noqa: E402


# --- D6: stale lock-file sweep (archive-move, never delete) --------------------
#
# NOTE (A1 coordination): SPEC A1 also adds a lock-file sweep (its step 7). A1
# had not shipped when CLEAN1 landed, so the sweep lives here. Whichever of A1 /
# CLEAN1 ships SECOND must verify this implementation exists and SKIP adding a
# duplicate sweep — there must be exactly one owner of the stale-lock sweep.
#
# Stale locks are sentinels renamed by atomic_write.release_write_lock() when it
# can't cleanly unlink its own lock; they take the shape
# `<file>.lock.stale.<epoch>.<pid>`. We age them off by filesystem mtime (NOT the
# embedded epoch) so a clock skew on the writing host can't strand them.
#
# ARCHIVE-ONLY (Command Room build, 2026-06): aged sentinels are MOVED into
# `_archive/stale-locks/` (mirroring their workspace-relative path), never
# unlinked. Same selection logic, same return value (the original relative paths
# that were cleared from their working spot) — only the disposition changed from
# delete to archive.
_LOCK_GLOB = "*.lock.stale.*"
_LOCK_DIRS = ("_hq/data", "_hq/.system")
_ARCHIVE_ROOT = "_archive"


def _archive_move(root: Path, src: Path, bucket: str) -> bool:
    """Move `src` into `root/_archive/<bucket>/<workspace-relative-path>`,
    preserving the original sub-path so an archived file is always traceable back
    to where it lived. Creates parents as needed. Returns True on success.

    Never overwrites: if a same-named archive entry already exists, a numeric
    suffix is appended so no archived copy is ever clobbered (still no delete)."""
    import shutil

    try:
        rel = src.relative_to(root)
    except ValueError:
        rel = Path(src.name)
    dest = root / _ARCHIVE_ROOT / bucket / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        stem, suffix, n = dest.stem, dest.suffix, 1
        while dest.exists():
            dest = dest.parent / f"{stem}.{n}{suffix}"
            n += 1
    try:
        shutil.move(str(src), str(dest))
        return True
    except OSError:
        return False


def sweep_stale_locks(root: str | Path, max_age_s: int = 3600,
                      now: float | None = None) -> list[str]:
    """Archive `*.lock.stale.*` sentinels older than `max_age_s` (default 1 hour)
    under `_hq/data/` and `_hq/.system/` by MOVING them into
    `_archive/stale-locks/` — nothing is deleted. Returns the workspace-relative
    paths cleared from their working spot. Fresh sentinels (a writer may still be
    mid-recovery) are preserved in place.

    Idempotent: a second run finds nothing to archive (the moved sentinels now
    live under `_archive/`, outside the `_LOCK_DIRS` it scans)."""
    root = Path(root)
    cutoff_now = time.time() if now is None else now
    archived: list[str] = []
    for rel in _LOCK_DIRS:
        d = root / rel
        if not d.is_dir():
            continue
        for f in d.glob(_LOCK_GLOB):
            if not f.is_file():
                continue
            try:
                age = cutoff_now - f.stat().st_mtime
            except OSError:
                continue
            if age > max_age_s:
                src_rel = str(f.relative_to(root)).replace("\\", "/")
                if _archive_move(root, f, "stale-locks"):
                    archived.append(src_rel)
    return archived


# --- LB2 D5: stale readalarm-sidecar pruning (the ONE delete exception) --------
#
# read_alarm.py sidecars (`<file>.readalarm.json`) are BY DESIGN never cleared
# on a clean read — evidence preservation — and only age out of the SURFACED
# view (read_alarm.RECENT_HOURS). Nothing ever pruned the files themselves, so
# they accumulate forever next to substrate files. LB2 D5 (M ruling
# 2026-07-19): cleanup's hygiene phase DELETES sidecars whose `last_seen` is
# older than 30 days — delete, not archive, breaking the module's ARCHIVE-ONLY
# doctrine for exactly this class because a sidecar is DERIVED ALARM STATE
# (machine telemetry about a past read failure), never substrate and never a
# user's file. Everything the archive-only policy protects stays protected.
# Hard floor regardless of arguments: a sidecar younger than RECENT_HOURS x 2
# is never deleted — its evidence must have been surfaceable by at least one
# brief/system-health fire first.

def prune_stale_readalarms(root: str | Path, max_age_days: int = 30,
                           now: float | None = None) -> list[str]:
    """Delete `*.readalarm.json` sidecars under `_hq/` whose recorded
    `last_seen` (fallback: file mtime, for an unreadable sidecar) is older
    than `max_age_days`. Returns the workspace-relative paths deleted.
    Floor: never deletes a sidecar younger than read_alarm.RECENT_HOURS * 2.
    Idempotent — a second run finds nothing."""
    import json as _json

    from read_alarm import RECENT_HOURS

    root = Path(root)
    hq = root / "_hq"
    if not hq.is_dir():
        return []
    cutoff_now = time.time() if now is None else now
    floor_s = RECENT_HOURS * 2 * 3600
    max_age_s = max(max_age_days * 86400, floor_s)
    deleted: list[str] = []
    for f in hq.rglob("*.readalarm.json"):
        if not f.is_file():
            continue
        age_ref = None
        try:
            data = _json.loads(f.read_text(encoding="utf-8"))
            seen = str((data or {}).get("last_seen") or "")
            if seen:
                s = seen.replace("Z", "+00:00")
                dt = datetime.datetime.fromisoformat(s)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=datetime.timezone.utc)
                age_ref = dt.timestamp()
        except (OSError, ValueError, _json.JSONDecodeError):
            pass
        if age_ref is None:
            try:
                age_ref = f.stat().st_mtime
            except OSError:
                continue
        if (cutoff_now - age_ref) > max_age_s:
            rel = str(f.relative_to(root)).replace("\\", "/")
            try:
                f.unlink()
                deleted.append(rel)
            except OSError:
                continue
    return deleted


# --- D3: missing SESSION_NOTES scaffold backfill -------------------------------
_DEFAULT_TEMPLATE = ROOT.parent.parent / "references" / "session-notes-template.md"


def _name_token(folder_name: str) -> str:
    """Derive the `[NAME]` token used in SESSION_NOTES_[NAME].md. Mirrors the
    workspace convention of keeping the human folder name (spaces allowed)."""
    return folder_name.strip()


def _render_scaffold(project_display: str, today: str, template_path: Path | None) -> str:
    tpl_path = template_path or _DEFAULT_TEMPLATE
    try:
        tpl = tpl_path.read_text(encoding="utf-8")
    except OSError:
        # Self-contained fallback so the backfill never fails on a missing
        # template file (mirrors the minimal content D3 specifies).
        tpl = ("# Session Notes — {{PROJECT}}\n\n"
               "> Backfilled by cleanup on {{DATE}} — no prior session notes existed.\n\n"
               "## Current Status\n\n## Active Work Items\n")
    return tpl.replace("{{PROJECT}}", project_display).replace("{{DATE}}", today)


def backfill_session_notes(root: str | Path, folder_name: str,
                           project_display: str | None = None,
                           today: str | None = None,
                           template_path: str | Path | None = None) -> str | None:
    """Scaffold a SESSION_NOTES file for a project folder that has none.

    CLIENT SAFETY: if ANY `SESSION_NOTES*.md` already exists in the folder, this
    returns None and writes nothing — an existing notes file is never
    overwritten. Returns the workspace-relative path created, or None.

    The scaffold carries the backfill provenance line so it's obvious the file
    was machine-created and is safe for the next "end session" to fill in."""
    root = Path(root)
    folder = root / folder_name
    if not folder.is_dir():
        return None
    # Never overwrite — any existing notes file (live / archive / index) wins.
    for _ in folder.glob("SESSION_NOTES*.md"):
        return None
    today = today or datetime.date.today().isoformat()
    target = folder / f"SESSION_NOTES_{_name_token(folder_name)}.md"
    content = _render_scaffold(project_display or folder_name, today,
                              Path(template_path) if template_path else None)
    atomic_write_text(target, content)
    return str(target.relative_to(root)).replace("\\", "/")


# --- D4: changed-only DECISION_LOG regeneration --------------------------------
def regenerate_decision_log_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/DECISION_LOG.md` from the substrate, writing only if
    the decision content changed (SPEC CLEAN1 / D4). Thin wrapper over the view's
    owner so cleanup records into actions_taken ONLY when `changed` is True."""
    return render_decision_log.regenerate_if_changed(root)


def regenerate_master_tracker_if_changed(root: str | Path) -> dict[str, Any]:
    """Regenerate `_hq/views/MASTER_TRACKER.md` (+ back-compat copy) from the
    substrate, writing only if the tracker content changed (SPEC CLEAN1 / D4).
    Thin wrapper over the view's owner so cleanup records into actions_taken
    ONLY when `changed` is True. This is the weekly backstop for the tracker
    freeze: end-session regenerates it per session, but a missed regen (the LLM
    end-session hand-render that froze M's tracker from 2026-06-11) would
    otherwise persist for weeks until the next end-session caught it."""
    return render_master_tracker.regenerate_if_changed(root)


# --- D5 / D7: read-only staleness flags ----------------------------------------
# Analytical views are owned by insight-generator (per references/VIEW_GENERATION.md);
# cleanup never regenerates them — it FLAGS staleness and names the owner.
_ANALYTICAL_VIEWS = (
    "RELATIONSHIPS.md", "TIMELINE.md", "COMMITMENT_AGING.md", "DORMANT.md", "THEMES.md",
)
_ANALYTICAL_OWNER = "insight-generator"
_ALIASES_OWNER = "people-crm"


def _substrate_mtime(root: Path) -> float:
    data = root / "_hq" / "data"
    mtimes = [p.stat().st_mtime for p in (data / "events.jsonl", data / "entities.json")
              if p.is_file()]
    return max(mtimes) if mtimes else 0.0


def check_analytical_view_staleness(root: str | Path,
                                    now: float | None = None) -> list[dict]:
    """Flag analytical views older than the substrate they derive from (D5).

    Read-only. Returns one dict per stale view: {view, owner, view_mtime,
    source_mtime}. cleanup turns these into a single Monday-note line naming
    insight-generator as the owner — it NEVER regenerates them (that's
    insight-generator's expensive synthesis)."""
    root = Path(root)
    src_mtime = _substrate_mtime(root)
    if src_mtime == 0.0:
        return []
    views_dir = root / "_hq" / "views"
    stale: list[dict] = []
    for name in _ANALYTICAL_VIEWS:
        v = views_dir / name
        if not v.is_file():
            continue  # never-generated views aren't "stale"; insight-generator owns first-gen
        if v.stat().st_mtime < src_mtime:
            stale.append({
                "view": name,
                "owner": _ANALYTICAL_OWNER,
                "view_mtime": v.stat().st_mtime,
                "source_mtime": src_mtime,
            })
    return stale


def _insight_last_run_epoch(root: Path) -> float | None:
    """Most recent insight-generator fire, derived from events.jsonl + the
    insights output dir. Returns an epoch seconds float, or None if never run.

    Signals (any of): a `pack_run` event whose kind/source is insight-flavored,
    an insight-pass event type, or the mtime of the newest `_hq/insights/` doc."""
    import json

    latest: float | None = None
    events = root / "_hq" / "data" / "events.jsonl"
    _INSIGHT_TYPES = {"insight", "classification_review", "project_proposal", "org_proposal"}
    if events.is_file():
        for line in events.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(ev, dict):
                continue
            t = ev.get("type")
            data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
            is_insight = t in _INSIGHT_TYPES
            if t == "pack_run":
                kind = str(data.get("kind") or ev.get("kind") or "")
                src = str(data.get("source_skill") or ev.get("source_skill") or "")
                if "insight" in kind.lower() or "insight" in src.lower():
                    is_insight = True
            if not is_insight:
                continue
            ts = event_time(ev)
            ep = _parse_ts_epoch(ts)
            if ep is not None and (latest is None or ep > latest):
                latest = ep
    insights_dir = root / "_hq" / "insights"
    if insights_dir.is_dir():
        for f in insights_dir.iterdir():
            if f.is_file():
                try:
                    m = f.stat().st_mtime
                except OSError:
                    continue
                if latest is None or m > latest:
                    latest = m
    return latest


def _parse_ts_epoch(ts: Any) -> float | None:
    if not isinstance(ts, str) or not ts:
        return None
    s = ts.strip().replace("Z", "+00:00")
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    # date-only
    try:
        return datetime.datetime.fromisoformat(s[:10]).timestamp()
    except ValueError:
        return None


def insight_generator_staleness(root: str | Path, now: float | None = None,
                                threshold_days: int = 14) -> dict:
    """Has insight-generator gone quiet for >threshold_days? (D5 nudge condition.)

    Read-only. Returns {stale: bool, last_run_epoch: float|None, age_days,
    threshold_days}. Used to add a Monday-note nudge so a paused insight-generator
    (the forensic root cause of the stale analytical views) surfaces plainly."""
    root = Path(root)
    cutoff_now = time.time() if now is None else now
    last = _insight_last_run_epoch(root)
    if last is None:
        return {"stale": True, "last_run_epoch": None, "age_days": None,
                "threshold_days": threshold_days, "reason": "never"}
    age_days = (cutoff_now - last) / 86400.0
    return {"stale": age_days > threshold_days, "last_run_epoch": last,
            "age_days": age_days, "threshold_days": threshold_days}


def check_aliases_staleness(root: str | Path) -> dict | None:
    """Flag ALIASES.md if it's older than aliases.json (SPEC CLEAN1 / D7).

    There is no standalone aliases-view renderer in shared/scripts (the view is
    owned by people-crm, which regenerates it on any aliases.json write). So this
    is a FLAG-only safety net — cleanup names people-crm as the owner and never
    regenerates the view itself. Returns a dict when stale/missing, else None."""
    root = Path(root)
    aliases_json = root / "_hq" / "data" / "aliases.json"
    view = root / "_hq" / "views" / "ALIASES.md"
    if not aliases_json.is_file():
        return None
    if not view.is_file():
        return {"view": "ALIASES.md", "owner": _ALIASES_OWNER, "reason": "missing"}
    if view.stat().st_mtime < aliases_json.stat().st_mtime:
        return {"view": "ALIASES.md", "owner": _ALIASES_OWNER, "reason": "stale",
                "view_mtime": view.stat().st_mtime,
                "source_mtime": aliases_json.stat().st_mtime}
    return None
