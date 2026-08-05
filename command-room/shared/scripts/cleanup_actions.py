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
  rollover_session_notes(root, threshold_lines=150, keep_entries=5, today=None) -> list[dict]
  regenerate_decision_log_if_changed(root) -> dict
  check_analytical_view_staleness(root, now=None) -> list[dict]
  insight_generator_staleness(root, now=None, threshold_days=14) -> dict
  check_aliases_staleness(root) -> dict | None
"""
from __future__ import annotations

import datetime
import re
import time
from collections import Counter
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


def _never_clobber(dest: Path) -> Path:
    """The archive tree's never-overwrite rule, in ONE place: if `dest` is
    taken, append a numeric suffix until it isn't. Shared by `_archive_move`
    (the lock sweep) and the rollover's pre-reshape safety copy, so both obey
    the same guarantee — an archived copy is never clobbered by a later one."""
    if not dest.exists():
        return dest
    stem, suffix, n = dest.stem, dest.suffix, 1
    while dest.exists():
        dest = dest.parent / f"{stem}.{n}{suffix}"
        n += 1
    return dest


def _archive_dest(root: Path, src: Path, bucket: str) -> Path:
    """Where `src` lands under `root/_archive/<bucket>/`, mirroring its
    workspace-relative path so an archived file is always traceable back to
    where it lived. Creates the parent; applies the never-clobber suffix."""
    try:
        rel = src.relative_to(root)
    except ValueError:
        rel = Path(src.name)
    dest = root / _ARCHIVE_ROOT / bucket / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    return _never_clobber(dest)


def _archive_move(root: Path, src: Path, bucket: str) -> bool:
    """Move `src` into `root/_archive/<bucket>/<workspace-relative-path>`,
    preserving the original sub-path so an archived file is always traceable back
    to where it lived. Creates parents as needed. Returns True on success.

    Never overwrites: if a same-named archive entry already exists, a numeric
    suffix is appended so no archived copy is ever clobbered (still no delete)."""
    import shutil

    dest = _archive_dest(root, src, bucket)
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


def backfill_session_notes(root: str | Path, folder_name: str | None,
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
    # `folder_name` is nullable on a thread record since FOLDERGUARD (the writer
    # emits null rather than guessing), and maintenance-rules.md documents this
    # call as taking one. `root / None` is a TypeError, not a refusal.
    # Whitespace-only is rejected explicitly: Windows strips trailing spaces from
    # a path, so `root / "   "` silently normalizes to the workspace root itself
    # and the scaffold would land there.
    if not folder_name or not str(folder_name).strip():
        return None
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


# --- MAINTAUTO1: scripted Rule 1 session-notes rollover ------------------------
#
# WHY THIS IS CODE AND NOT PROSE. Rule 1 (rollover a SESSION_NOTES file past 150
# lines) shipped as prose in two places while every maintenance action that
# actually executes every week is a python call with a receipt. Prose gets
# second-guessed at fire time; code blocks run. A live 2026-08-03 maintenance
# fire found ten notes files past the threshold — the largest thirteen times
# over — and the fire-time model declared rollover an attended-only step, a
# holdback that appears NOWHERE in the shipped contract. This function is the
# implementation the prose always claimed existed.
#
# THE DOCTRINE IT INHERITS. Archive-only (entries MOVE into a sibling archive
# file, nothing is ever deleted), idempotent (a rolled-over file re-runs to zero
# writes), atomic (`atomic_write_text`), substrate-blind (never reads or writes
# entities.json / events.jsonl). One addition of its own: EVERY reshape is
# preceded by a byte-copy of the original into `_archive/`, so the whole first
# pass over a years-deep backlog is one move away from being undone.
#
# MECHANICAL, NEVER INTERPRETIVE. The split is a pure text partition: H2 blocks
# whose heading carries a parseable date are session-log entries; everything
# else is kept verbatim where it sits. The Session-History and index lines are
# derived deterministically from the entry's own first content line — an LLM may
# polish them afterwards, but execution never waits on one.
#
# AND WHEN IT CANNOT TELL, IT DOES NOTHING. Real notes files drift: entries nest
# under H3, undated `## Meeting:` blocks sit between dated ones, an out-of-order
# append breaks chronology. Every one of those returns an `aborted` record and
# leaves the file byte-identical. A partial reshape of a CEO's memory is worse
# than no reshape (the STAFFCUT round-1 lesson: a fix that writes malformed
# output is worse than the drop it replaced), so the conservation check below
# re-derives that every original line survives into kept-or-archived output
# BEFORE the first byte is written, and abandons the file if it cannot.
_ROLLOVER_DEFAULT_LINES = 150
_ROLLOVER_DEFAULT_KEEP = 5
_PRE_ROLLOVER_BUCKET = "session-notes-pre-rollover"
# The one non-entry H2 the rollover itself authors, and therefore the one it
# must tolerate sitting inside the log region on every later run.
_SESSION_HISTORY_TITLE = "Session History"
_SUMMARY_WORDS = 8

_H2_RE = re.compile(r"^##[ \t]+(.*?)[ \t]*$")
_H3_RE = re.compile(r"^###[ \t]+(.*?)[ \t]*$")
_ISO_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_ISO_LEAD_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")
_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")
_LONG_DATE_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2}),\s*(\d{4})\b")
# Only a heading that ANNOUNCES itself as a session/meeting entry may have its
# date found mid-string. Without that fence `## Current Status (as of April 7,
# 2026 — Session 15 close)` — a live-state section in a real workspace file —
# reads as a dated entry and gets archived away.
_ENTRY_PREFIX_RE = re.compile(r"^(Session|Meeting)\b")

_ROLLOVER_SKIP_DIR_RE = re.compile(r"archive|backup", re.I)
# A markdown table's alignment rule, with or without leading/trailing pipes:
# `|---|---|`, `--- | :---:`, `:--|--:`. Rows themselves start with `|`.
_TABLE_RULE_RE = re.compile(r"^[|:\- ]*-{3,}[|:\- ]*$")


def _valid_date(y: int, m: int, d: int) -> str | None:
    try:
        return datetime.date(y, m, d).isoformat()
    except ValueError:
        return None


def _entry_date(title: str) -> str | None:
    """The entry date a session-log H2 heading carries, or None if the heading
    is not a dated entry. Three shapes, all observed live:

      `## 2026-08-01 — topic`            ISO leading the heading
      `## April 3, 2026 — Session 10`    long-form date leading the heading
      `## Session 18 close — May 13, 2026` / `## Session: 2026-04-16 (#21) — x`
                                        date anywhere, but ONLY when the
                                        heading opens with Session/Meeting
    """
    if _ISO_LEAD_RE.match(title):
        m = _ISO_DATE_RE.match(title)
        if m:
            return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = _LONG_DATE_RE.match(title)
    if m:
        return _valid_date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1,
                           int(m.group(2)))
    if _ENTRY_PREFIX_RE.match(title):
        m = _ISO_DATE_RE.search(title)
        if m:
            return _valid_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        m = _LONG_DATE_RE.search(title)
        if m:
            return _valid_date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1,
                               int(m.group(2)))
    return None


def _split_h2_blocks(lines: list[str]) -> list[dict]:
    """Partition `lines` at H2 boundaries. blocks[0] is the preamble (title
    None) and may be empty. The concatenation of every block's lines is EXACTLY
    the input — the conservation check downstream depends on that."""
    blocks: list[dict] = []
    cur: dict = {"title": None, "lines": []}
    for ln in lines:
        m = _H2_RE.match(ln)
        if m:
            blocks.append(cur)
            cur = {"title": m.group(1), "lines": [ln]}
        else:
            cur["lines"].append(ln)
    blocks.append(cur)
    return blocks


def _entry_summary(block: dict) -> str:
    """~8 words describing the entry, derived from its own text: the first
    non-empty body line of PROSE with markdown furniture stripped, falling back
    to the heading with its leading date removed. Deterministic — no LLM in the
    path.

    Table rows and their separator rules are skipped rather than summarised. An
    entry that opens with a table is common (a decisions grid, an owner list),
    and "/ Item / Owner / Status /" as the Session-History line for that day
    defeats the whole point of Rule 1's summary — it tells the CEO nothing
    about what happened, and it is the line they scan instead of the archive."""
    body = ""
    for ln in block["lines"][1:]:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("|"):
            continue                      # a table row
        if _TABLE_RULE_RE.match(s):
            continue                      # a header/alignment rule
        s = re.sub(r"^[-*+>]\s*", "", s)
        s = re.sub(r"^\d+[.)]\s*", "", s)
        s = s.replace("**", "").replace("`", "").replace("__", "").strip()
        if not s or s.startswith("|") or _TABLE_RULE_RE.match(s):
            continue
        if s:
            body = s
            break
    if not body:
        body = re.sub(r"^\S+\s*[-—:|]\s*", "", block["title"] or "").strip()
    if not body:
        body = (block["title"] or "").strip()
    words = body.split()
    text = " ".join(words[:_SUMMARY_WORDS])
    if len(words) > _SUMMARY_WORDS:
        text += "..."
    # A pipe would silently split the Rule 12 index row into extra columns.
    return text.replace("|", "/")


def _notes_name_token(path: Path) -> str:
    """`SESSION_NOTES_ACME.md` -> `ACME`; a bare `SESSION_NOTES.md` -> ``."""
    stem = path.stem
    if stem.upper().startswith("SESSION_NOTES_"):
        return stem[len("SESSION_NOTES_"):]
    return ""


def _sibling(path: Path, suffix: str) -> Path:
    token = _notes_name_token(path)
    base = f"SESSION_NOTES_{token}" if token else "SESSION_NOTES"
    return path.parent / f"{base}_{suffix}.md"


def _maintenance_overrides(root: Path) -> dict[str, str]:
    """The `CUSTOM_CONFIG.md` knobs maintenance-rules.md documents. Tolerant by
    design: any `key: value` line anywhere in the file counts, comments after
    `#` are dropped, and a missing/unreadable file means defaults."""
    out: dict[str, str] = {}
    for candidate in (root / "CUSTOM_CONFIG.md", root / "_hq" / "CUSTOM_CONFIG.md"):
        if not candidate.is_file():
            continue
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip().lstrip("-* ").strip()
            if ":" not in line:
                continue
            key, _, val = line.partition(":")
            key, val = key.strip(), val.strip()
            if re.fullmatch(r"[a-z0-9_]+", key) and val:
                out.setdefault(key, val)
    return out


def _iter_session_notes(root: Path):
    """Live SESSION_NOTES files, resolved the way the rest of the module treats
    project folders: anything under `_archive/`, a dot-directory, or a folder
    whose name says archive/backup is out; so is any file that IS an archive,
    an index, or the shipped template."""
    for p in sorted(root.rglob("SESSION_NOTES*.md")):
        try:
            rel = p.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts[:-1]
        if any(part.startswith(".") or part == _ARCHIVE_ROOT
               or _ROLLOVER_SKIP_DIR_RE.search(part) for part in parts):
            continue
        # Case-insensitive, matching the directory fence above: a workspace
        # that shouted its archive filename (`..._ARCHIVE_2026.md`) would
        # otherwise be reshaped as if it were the live file.
        name = p.name.upper()
        if "_ARCHIVE" in name or name.endswith("_INDEX.MD"):
            continue
        if "TEMPLATE" in name:
            continue
        if p.is_file():
            yield p


def _plan_rollover(lines: list[str], keep_entries: int) -> dict:
    """Classify one notes file. Returns either {"abort": reason, ...},
    {"skip": reason, ...} or a full plan. Reads only — no I/O, no writes."""
    blocks = _split_h2_blocks(lines)
    entry_idx: list[int] = []
    dates: dict[int, str] = {}
    for i, b in enumerate(blocks):
        if b["title"] is None:
            continue
        d = _entry_date(b["title"])
        if d:
            entry_idx.append(i)
            dates[i] = d
    if not entry_idx:
        nested = any(_entry_date(m.group(1))
                     for m in (_H3_RE.match(ln) for ln in lines) if m)
        return {"abort": "entries_below_h2" if nested else "no_recognized_entries",
                "detail": ("dated entries are nested under H3, a shape this "
                           "rollover does not reshape")
                          if nested else "no dated session entries found"}
    lo, hi = entry_idx[0], entry_idx[-1]
    known = set(entry_idx)
    stray = [blocks[i]["title"] for i in range(lo, hi + 1)
             if i not in known and blocks[i]["title"] != _SESSION_HISTORY_TITLE]
    if stray:
        return {"abort": "unrecognized_section_in_log",
                "detail": f"undated section between dated entries: {stray[0]!r}"}
    seq = [dates[i] for i in entry_idx]
    newest_first = all(a >= b for a, b in zip(seq, seq[1:]))
    oldest_first = all(a <= b for a, b in zip(seq, seq[1:]))
    if not (newest_first or oldest_first):
        # Neither monotone means BOTH an ascending and a descending pair exist;
        # name the ascending one — it is the out-of-order append.
        break_at = next((f"{a} then {b}" for a, b in zip(seq, seq[1:]) if a < b),
                        " / ".join(seq[:3]))
        return {"abort": "entries_unordered",
                "detail": f"entry dates are not in one chronological direction "
                          f"({break_at})"}
    if len(entry_idx) <= keep_entries:
        return {"skip": "nothing_to_archive", "entries": len(entry_idx)}
    archived = (entry_idx[keep_entries:] if newest_first
                else entry_idx[:len(entry_idx) - keep_entries])
    return {
        "blocks": blocks,
        "entry_idx": entry_idx,
        "dates": dates,
        "archived_idx": archived,
        "newest_first": newest_first,
    }


def _render_rollover(path: Path, plan: dict, today: str) -> dict:
    """Turn a plan into the exact text each file gets. Pure — still no writes.
    Returns {active_lines, archives: {Path: [lines to append]}, history_lines,
    index_rows, archive_names, generated}.

    `generated` is every line this function AUTHORED rather than carried over
    from the original: the Session-History section and its lines, the Rule 1
    reference lines, the archive headers, and the blank separators. The
    conservation check subtracts it before looking for losses, because a
    generated line that happens to read identically to an original one would
    otherwise pay for that original's disappearance — and the history lines are
    derived FROM the entries, so reading identically to a line inside one is
    not a freak coincidence but a predictable collision."""
    blocks, archived = plan["blocks"], set(plan["archived_idx"])
    token = _notes_name_token(path)
    history: list[str] = []
    index_rows: list[str] = []
    archives: dict[Path, list[str]] = {}
    ref_names: list[str] = []
    generated: list[str] = []
    for i in plan["archived_idx"]:
        b = blocks[i]
        date = plan["dates"][i]
        summary = _entry_summary(b)
        history.append(f"- {date}: {summary}")
        arch = _sibling(path, f"archive_{date[:4]}")
        if arch.name not in ref_names:
            ref_names.append(arch.name)
        archives.setdefault(arch, [])
        if archives[arch]:
            archives[arch].append("")
            generated.append("")
        archives[arch].extend(b["lines"])
        # People stays blank on purpose: an honest blank beats attribution the
        # parser would have to invent (Rule 12, MAINTAUTO1 D2).
        index_rows.append(f"| {date} | {summary} |  | {arch.name} |")

    hist_idx = next((i for i, b in enumerate(blocks)
                     if b["title"] == _SESSION_HISTORY_TITLE), None)
    hist_section = [f"## {_SESSION_HISTORY_TITLE}", ""] + history + [""]

    active: list[str] = []
    emitted_history = False
    for i, b in enumerate(blocks):
        if i in archived:
            if hist_idx is None and not emitted_history:
                active.extend(hist_section)
                generated.extend(hist_section)
                emitted_history = True
            continue
        if i == hist_idx:
            kept = list(b["lines"])
            while kept and not kept[-1].strip():
                kept.pop()
            active.extend(kept + history + [""])
            generated.extend(history + [""])
            continue
        active.extend(b["lines"])
    if hist_idx is None and not emitted_history:  # pragma: no cover - archived is non-empty
        active.extend(hist_section)
        generated.extend(hist_section)

    while active and not active[-1].strip():
        active.pop()
    for name in ref_names:
        ref = f"> Full entries archived to {name}"
        # Dedupe: a file rolled over twice into the same year's archive must
        # not accrue a second identical reference line every week.
        if ref not in active:
            active.extend(["", ref])
            generated.extend(["", ref])
    active.append("")
    generated.append("")

    for arch, body in archives.items():
        if not arch.is_file():
            head = [f"# Session Notes Archive — {token or path.stem}",
                    "",
                    f"> Rolled over from {path.name} by cleanup on {today} "
                    f"(maintenance Rule 1). Full entries, never summaries — "
                    f"nothing here was deleted from anywhere.",
                    ""]
            archives[arch] = head + body
            generated.extend(head)
    return {"active_lines": active, "archives": archives,
            "history_lines": history, "index_rows": index_rows,
            "archive_names": ref_names, "generated": generated}


def _conservation_residual(original: list[str], rendered: dict) -> list[str]:
    """Every original line of CONTENT must survive into kept-or-archived
    output. Counted as a MULTISET so a duplicated line cannot cover for a
    dropped twin — the shape of the failure this check exists to catch is a
    parser that mishandles ONE of several look-alike entries.

    THE OUTPUT IS NOT THE EVIDENCE. Counting the rendered files as-is would let
    the renderer pay its own debts: it authors Session-History lines DERIVED
    from the entries it is archiving, so a generated line reading identically
    to a line inside one of those entries is not a freak coincidence but a
    predictable collision — and Counter subtraction would then show the
    original line as still present while the archive lost it, writing the
    reshape with content gone. So `have` counts only what was CARRIED OVER:
    the rendered lines minus the exact multiset the renderer authored.

    Blank lines are excluded on purpose, and the exclusion is load-bearing
    rather than a shortcut. The renderer legitimately normalises vertical
    whitespace (it collapses trailing blanks before appending the history and
    reference lines), so counting blanks would produce false aborts. Saying
    plainly that this check protects content and not spacing is worth more than
    a stricter-looking check that is neither."""
    def content(lines) -> Counter:
        return Counter(ln for ln in lines if ln.strip())

    have: Counter = content(rendered["active_lines"])
    for body in rendered["archives"].values():
        have += content(body)
    have -= content(rendered.get("generated") or ())
    missing = content(original) - have
    return sorted(missing.elements())[:5]


def rollover_session_notes(root: str | Path,
                           threshold_lines: int = _ROLLOVER_DEFAULT_LINES,
                           keep_entries: int = _ROLLOVER_DEFAULT_KEEP,
                           today: str | None = None) -> list[dict]:
    """Roll over every SESSION_NOTES file past `threshold_lines` (Rule 1), and
    index what moved (Rule 12). AUTOMATIC — cleanup calls this without asking;
    the pre-reshape copy under `_archive/session-notes-pre-rollover/` is the
    permission.

    Per eligible file, in order: keep `## Current Status` / `## Active Work
    Items` and every other non-entry section exactly where they are, keep the
    `keep_entries` newest dated entries, move the older full entries into
    `SESSION_NOTES_[NAME]_archive_[YYYY].md` (appended, never overwritten), add
    one deterministic `## Session History` line per archived entry plus the
    Rule 1 step-5 reference line, and append Rule 12 index rows to
    `SESSION_NOTES_[NAME]_index.md`.

    Honors the `session_notes_rollover_lines` and `archive_index_enabled` knobs
    from `CUSTOM_CONFIG.md`. Rule 13 embeddings are out of scope.

    Returns one record per file it CONSIDERED (over threshold), never one for a
    file it never had cause to touch:

      {file, status: "rolled_over", entries_archived, archive_files, index_file,
       safety_copy, lines_before, lines_after}
      {file, status: "skipped",  reason}   -- over threshold, nothing to archive
      {file, status: "aborted",  reason, detail}   -- file left byte-identical

    Idempotent: a second run over a rolled-over workspace makes zero writes.
    """
    import shutil

    root = Path(root)
    if not root.is_dir():
        return []
    overrides = _maintenance_overrides(root)
    if threshold_lines == _ROLLOVER_DEFAULT_LINES:
        try:
            threshold_lines = int(overrides.get("session_notes_rollover_lines",
                                                threshold_lines))
        except (TypeError, ValueError):
            pass
    index_enabled = str(overrides.get("archive_index_enabled", "true")).lower() \
        not in ("false", "no", "0", "off")
    today = today or datetime.date.today().isoformat()
    records: list[dict] = []

    for path in _iter_session_notes(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        try:
            original_text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "unreadable", "detail": str(exc)[:120]})
            continue
        lines = original_text.splitlines()
        if len(lines) <= threshold_lines:
            continue

        plan = _plan_rollover(lines, keep_entries)
        if "abort" in plan:
            records.append({"file": rel, "status": "aborted",
                            "reason": plan["abort"],
                            "detail": plan.get("detail", "")})
            continue
        if "skip" in plan:
            records.append({"file": rel, "status": "skipped",
                            "reason": plan["skip"],
                            "entries": plan.get("entries")})
            continue

        rendered = _render_rollover(path, plan, today)
        residual = _conservation_residual(lines, rendered)
        if residual:
            # The high-stakes branch: the split lost content, so this file gets
            # NOTHING written to it. Not a partial reshape, not a best guess.
            records.append({
                "file": rel, "status": "aborted", "reason": "conservation_failed",
                "detail": f"{len(residual)}+ original line(s) would be lost, "
                          f"first: {residual[0][:80]!r}"})
            continue

        # Safety copy FIRST — nothing below this line runs without a byte-exact
        # original sitting in `_archive/`.
        try:
            copy_dest = _archive_dest(root, path, _PRE_ROLLOVER_BUCKET)
            shutil.copy2(str(path), str(copy_dest))
        except OSError as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "safety_copy_failed",
                            "detail": str(exc)[:120]})
            continue

        try:
            for arch, body in rendered["archives"].items():
                prior = arch.read_text(encoding="utf-8") if arch.is_file() else ""
                if prior and not prior.endswith("\n"):
                    prior += "\n"
                atomic_write_text(arch, prior + "\n".join(body) + "\n",
                                  create_parents=False)
            atomic_write_text(path, "\n".join(rendered["active_lines"]) + "\n",
                              create_parents=False)
            index_rel = None
            if index_enabled:
                index = _sibling(path, "index")
                if index.is_file():
                    prior = index.read_text(encoding="utf-8")
                    if prior and not prior.endswith("\n"):
                        prior += "\n"
                else:
                    token = _notes_name_token(path) or path.stem
                    prior = (f"# Session Notes Index — {token}\n\n"
                             "| Date | Topics | People | Archive File |\n"
                             "|------|--------|--------|-------------|\n")
                atomic_write_text(index,
                                  prior + "\n".join(rendered["index_rows"]) + "\n",
                                  create_parents=False)
                index_rel = str(index.relative_to(root)).replace("\\", "/")
        except OSError as exc:
            records.append({"file": rel, "status": "aborted",
                            "reason": "write_failed", "detail": str(exc)[:120],
                            "safety_copy": str(copy_dest.relative_to(root)
                                               ).replace("\\", "/")})
            continue

        records.append({
            "file": rel,
            "status": "rolled_over",
            "entries_archived": len(plan["archived_idx"]),
            "archive_files": rendered["archive_names"],
            "index_file": index_rel,
            "safety_copy": str(copy_dest.relative_to(root)).replace("\\", "/"),
            "lines_before": len(lines),
            "lines_after": len(rendered["active_lines"]),
        })
    return records


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
