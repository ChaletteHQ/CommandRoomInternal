#!/usr/bin/env python3
"""Sync conflict copies of the book: find them, merge them back, archive them.

THE FAULT (cr1#724, cr1#385). A seat used from two computers through a sync
service (OneDrive, Google Drive, Dropbox, Syncthing) can end up with the
other computer's version of a substrate file kept beside the live one under
the service's own conflict name: `events-<device>.jsonl`, `entities (1).json`,
`CLAUDE (conflicted copy ...).md`. Every surface reads the live file only, so
whatever the other computer wrote while out of sync vanishes silently. The
older reconciler (`reconcile_forward`) knew the Google Drive spelling only.

WHAT THIS DOES, on every scheduled Maintenance fire (the sweep rides the
fire-start marker, `maintenance_dispatcher._sync_conflict_sweep`) or by
hand (`python3 sync_conflict_copies.py --workspace <ws> --apply`):

  1. DETECT every conflict copy beside a live substrate file (`events.jsonl`,
     `entities.json`, `aliases.json`, their `.seqhw` / `.rev` sidecars), the
     brain file `CLAUDE.md` and the per-project `SESSION_NOTES_*.md` files.
  2. The activity log: find the entries only the copy holds (by CONTENT, never
     by number: both sides kept numbering after the fork) and append them
     through the one append path with fresh numbers, their original time,
     in their original order, each carrying `data.recovered_from` naming the
     copy and its old number. A `supersedes_seq` between two merged entries
     is renumbered with them. Never rewrites or reorders the live log.
  3. The entity file: a record only the copy holds is created through the
     canonical writer (threads keep their id; people get a fresh one and the
     mapping is in the manifest); a record both sides hold with different
     fields is NOT guessed at: it is listed for review in the manifest and
     on the receipt.
  4. The text files: lines only the copy holds are appended under a dated
     heading, line-wise, because these notes are append-only addenda. The
     generated LIVE-STATE blocks of `CLAUDE.md` are never taken from a copy.
  5. ARCHIVE every copy into `_archive/cleanup-<date>/sync-conflict-copies/`
     with a `MANIFEST.md`. Nothing is deleted.
  6. ONE receipt (`sync_conflicts_run`) with the counts and the one sentence
     the brief can say.

THE ONE REFUSAL (cr1#385's overwrite case). When the copy of the activity log
holds EVERYTHING the live file holds and more, the live file may be the stale
one (a trailing computer flushed an old copy over the newer one). That is not
a merge; it is a question for the owner. Nothing is merged, the copy stays
where it is, the receipt says `held`, and system-health names it.

No client, personal or money information lives in this module; names in
tests follow `references/PRIVACY_POLICY.md`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

JOB_ID = "sync-conflicts"
RECEIPT_TYPE = "sync_conflicts_run"
SOURCE_SKILL = "sync-conflict-copies"

#: The live substrate files (under `_hq/data`) a copy can sit beside.
DATA_FILES = ("events.jsonl", "entities.json", "aliases.json",
              "events.jsonl.seqhw", "entities.json.rev", "aliases.json.rev")

#: The marker `claude_md_guard` treats as machine-owned: never merged from a
#: copy (the copy's block is older content by construction).
_LIVE_STATE_RE = re.compile(
    r"<!--\s*LIVE-STATE:.*?/LIVE-STATE:[^>]*-->", re.S | re.IGNORECASE)

#: A device tag OneDrive appends: `<stem>-<ComputerName><ext>`. Four bare
#: digits are a year shard (`events-2025.jsonl`), never a device.
_ONEDRIVE_TAG_RE = re.compile(r"^-(?!\d{4}$)[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}$")
#: A hand-made sibling (`entities-backup.json`, `CLAUDE-notes.md`) is not a
#: sync copy; these words after the dash are never read as a computer name.
_HAND_SUFFIXES = frozenset({"backup", "bak", "old", "copy", "notes", "draft",
                            "test", "archive", "archived", "orig", "original",
                            "new", "tmp", "temp", "v1", "v2", "v3"})
_DRIVE_TAG_RE = re.compile(r"^ \(\d+\)$")
_DROPBOX_TAG_RE = re.compile(r"^ \(.*conflicted copy.*\)$", re.IGNORECASE)
_SYNCTHING_TAG_RE = re.compile(r"^\.sync-conflict-\d{8}-\d{6}-[A-Za-z0-9]+$")

#: What a seq-bearing reference between two ledger rows is spelled as
#: (`events.schema.json` names exactly this one).
_SEQ_REF_FIELDS = ("supersedes_seq",)

#: Fields whose difference between two copies of one entity record is noise,
#: not a fork: both computers stamp them on every write.
_VOLATILE_RECORD_KEYS = frozenset({"last_updated", "updated_at", "last_writer",
                                   "version", "last_interaction", "last_seen"})

HELD_LIVE_STALE = "live_stale"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _split_name(live_name: str) -> Tuple[str, str]:
    """`events.jsonl` -> (`events`, `.jsonl`); `events.jsonl.seqhw` ->
    (`events.jsonl`, `.seqhw`). The sync services keep the LAST extension and
    put their tag in front of it."""
    stem, ext = os.path.splitext(live_name)
    return stem, ext


def copy_tag(live_name: str, candidate_name: str) -> Optional[str]:
    """The sync service's tag when `candidate_name` is a conflict copy of
    `live_name`, else None. A quarantine, a `.bak_`, a year shard, a
    regression marker or the live file itself is never a copy."""
    if candidate_name == live_name:
        return None
    if candidate_name == live_name + ".conflict":
        return ".conflict"
    stem, ext = _split_name(live_name)
    if not candidate_name.startswith(stem) or not candidate_name.endswith(ext):
        return None
    middle = candidate_name[len(stem):len(candidate_name) - len(ext)] if ext else candidate_name[len(stem):]
    if not middle:
        return None
    if _ONEDRIVE_TAG_RE.match(middle) and middle[1:].strip().lower() in _HAND_SUFFIXES:
        return None
    for rx in (_ONEDRIVE_TAG_RE, _DRIVE_TAG_RE, _DROPBOX_TAG_RE, _SYNCTHING_TAG_RE):
        if rx.match(middle):
            return middle
    return None


def conflict_copies_of(live_path) -> List[Path]:
    """Every conflict copy sitting beside `live_path`, sorted by name."""
    live = Path(live_path)
    d = live.parent
    if not d.is_dir():
        return []
    out = []
    try:
        names = os.listdir(d)
    except OSError:
        return []
    for name in names:
        p = d / name
        if not p.is_file():
            continue
        if copy_tag(live.name, name) is not None:
            out.append(p)
    return sorted(out)


def _session_notes(workspace_root: Path) -> List[Path]:
    """The LIVE per-project session-notes files (`<project>/SESSION_NOTES_*.md`):
    a notes file that is a conflict copy of a sibling is not one of them."""
    out = []
    try:
        for child in workspace_root.iterdir():
            if not child.is_dir() or child.name.startswith((".", "_")):
                continue
            names = sorted(p.name for p in child.glob("SESSION_NOTES_*.md") if p.is_file())
            for name in names:
                is_copy = any(copy_tag(other, name) is not None
                              for other in names if other != name)
                if not is_copy:
                    out.append(child / name)
    except OSError:
        pass
    return out


def scan(workspace_root) -> Dict[str, Any]:
    """Read-only: `{copies: [{live, copy, kind, tag}], n}` for the workspace.
    `kind` is `events`, `entities`, `aliases`, `sidecar` or `text`."""
    root = Path(workspace_root)
    data = root / "_hq" / "data"
    found: List[Dict[str, Any]] = []
    for name in DATA_FILES:
        live = data / name
        for c in conflict_copies_of(live):
            kind = ("sidecar" if name.endswith((".seqhw", ".rev"))
                    else name.split(".")[0])
            found.append({"live": str(live), "copy": str(c), "kind": kind,
                          "tag": copy_tag(name, c.name)})
    text_lives = [root / "CLAUDE.md"] + _session_notes(root)
    for live in text_lives:
        for c in conflict_copies_of(live):
            found.append({"live": str(live), "copy": str(c), "kind": "text",
                          "tag": copy_tag(live.name, c.name)})
    return {"copies": found, "n": len(found)}


# ---------------------------------------------------------------------------
# The activity log
# ---------------------------------------------------------------------------

def _parse_jsonl(path: Path) -> List[dict]:
    out: List[dict] = []
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


def _sig(ev: dict) -> tuple:
    """Content signature that ignores the number: `{ts, type, source_skill,
    sha(data without the recovery stamp)}` (the reconciler's own key)."""
    data = ev.get("data")
    if isinstance(data, dict) and "recovered_from" in data:
        data = {k: v for k, v in data.items()
                if k not in ("recovered_from", "recovered_by")}
    try:
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        blob = repr(data)
    return (ev.get("ts"), ev.get("type"), ev.get("source_skill"),
            hashlib.sha256(blob.encode("utf-8")).hexdigest())


def _commit_id(ev: dict) -> Optional[str]:
    if ev.get("type") != "commitment":
        return None
    data = ev.get("data")
    if isinstance(data, dict):
        i = data.get("id")
        if isinstance(i, str) and i.strip():
            return i.strip()
    return None


def _seq_of(ev: dict) -> Optional[int]:
    s = ev.get("seq")
    if isinstance(s, bool) or not isinstance(s, (int, float)):
        return None
    if s >= 10_000_000_000:
        return None
    return int(s)


def plan_events(live_path, copy_path) -> Dict[str, Any]:
    """What a merge of `copy_path` into `live_path` would do, without doing
    it: `{fork_at, n_live, n_copy, n_shared, missing: [events], held}`.
    `held` is `live_stale` when the copy holds everything the live file holds
    and more (cr1#385's case: the live file may be the older one)."""
    live = _parse_jsonl(Path(live_path))
    copy = _parse_jsonl(Path(copy_path))
    live_sigs = {_sig(e) for e in live}
    live_ids = {cid for e in live if (cid := _commit_id(e))}
    fork_at = 0
    for a, b in zip(live, copy):
        if _sig(a) != _sig(b):
            break
        fork_at += 1
    missing: List[dict] = []
    seen: set = set()
    n_receipts = 0
    for ev in copy:
        s = _sig(ev)
        if s in live_sigs or s in seen:
            continue
        cid = _commit_id(ev)
        if cid is not None and cid in live_ids:
            continue
        seen.add(s)
        if _is_run_record(ev):
            # The other computer's run receipts and markers are its own
            # history, never the book: replayed with a fresh high number they
            # would read as this computer's NEWEST fire (the hand repair on
            # the live seat skipped the same rows).
            n_receipts += 1
            continue
        missing.append(ev)
    copy_sigs = {_sig(e) for e in copy}
    # Only the live log's own WORK counts as its own: a receipt a fire wrote
    # on top of a stale copy (this job's held receipt, a pack_run) must not
    # turn "the live file may be the older one" into a merge.
    live_only = [e for e in live
                 if _sig(e) not in copy_sigs and not _is_run_record(e)]
    live_own = [e for e in live if not _is_run_record(e)]
    copy_own = [e for e in copy if not _is_run_record(e)]
    held = None
    if missing and not live_only and len(copy_own) > len(live_own):
        held = HELD_LIVE_STALE
    return {"fork_at": fork_at, "n_live": len(live), "n_copy": len(copy),
            "n_shared": len(copy) - len(missing) - n_receipts, "missing": missing,
            "n_missing": len(missing), "n_receipts_skipped": n_receipts,
            "held": held}


#: Run markers that are never the book's content (beside every receipt type
#: the receipt contract registers): the clock and sequence telemetry, the
#: reconciler's own receipt, a stop record.
_RUN_RECORD_TYPES = frozenset({
    RECEIPT_TYPE, "substrate_reconciled", "substrate_alarm_cleared",
    "late_fire", "clock_untrusted", "fire_stopped", "schedule_skipped",
    "schedule_refreshed", "seq_repaired", "seq_gap_marked"})


def _receipt_types() -> frozenset:
    try:
        from receipts import ALL_RECEIPT_TYPES
        return frozenset(ALL_RECEIPT_TYPES) | _RUN_RECORD_TYPES
    except Exception:  # noqa: BLE001
        return _RUN_RECORD_TYPES


def _is_run_record(ev: dict) -> bool:
    return str(ev.get("type") or "") in _receipt_types()


def _file_max_seq(path: Path) -> int:
    m = 0
    for ev in _parse_jsonl(path):
        s = _seq_of(ev)
        if s is not None and s > m:
            m = s
    return m


def merge_events(workspace_root, copy_path, *, apply: bool,
                 holder: str = SOURCE_SKILL) -> Dict[str, Any]:
    """Append the entries only the copy holds, through the one append path.
    Returns the plan plus `{n_recovered, refused: [...], seq_map,
    reference_drift}`. With `apply` False nothing is written."""
    root = Path(workspace_root)
    live = root / "_hq" / "data" / "events.jsonl"
    copy = Path(copy_path)
    plan = plan_events(live, copy)
    out = dict(plan, n_recovered=0, refused=[], seq_map={},
               reference_drift=False, copy=copy.name)
    out.pop("missing", None)
    if plan["held"] or not plan["missing"] or not apply:
        return out
    from event_gate import append_event

    base = _file_max_seq(live)
    planned: List[dict] = []
    seq_map: Dict[int, int] = {}
    next_seq = base + 1
    for ev in plan["missing"]:
        old = _seq_of(ev)
        if old is not None:
            seq_map[old] = next_seq
        next_seq += 1
    next_seq = base + 1
    for ev in plan["missing"]:
        row = {k: v for k, v in ev.items() if k != "seq"}
        data = row.get("data")
        if not isinstance(data, dict):
            out["refused"].append({"seq": _seq_of(ev), "reason": "data is not an object"})
            continue
        data = dict(data)
        for f in _SEQ_REF_FIELDS:
            ref = data.get(f)
            if isinstance(ref, int) and not isinstance(ref, bool) and ref in seq_map:
                data[f] = seq_map[ref]
        data["recovered_from"] = {"copy": copy.name, "seq": _seq_of(ev)}
        data["recovered_by"] = holder
        row["data"] = data
        row["seq"] = next_seq
        next_seq += 1
        planned.append(row)
    stamped: List[dict] = []
    try:
        stamped = append_event(live, planned, holder=holder) if planned else []
    except Exception as exc:  # noqa: BLE001 - fall back to one at a time
        batch_err = f"{type(exc).__name__}: {exc}"
        stamped = []
        for row in planned:
            try:
                stamped.extend(append_event(live, [row], holder=holder))
            except Exception as exc2:  # noqa: BLE001
                out["refused"].append({
                    "seq": row["data"]["recovered_from"]["seq"],
                    "reason": f"{type(exc2).__name__}: {exc2}"[:200]})
        out["batch_error"] = batch_err[:200]
    got = [_seq_of(s) for s in stamped]
    want = [r["seq"] for r in planned][:len(got)]
    out["reference_drift"] = bool(seq_map) and got != want
    out["n_recovered"] = len(stamped)
    out["seq_map"] = {str(k): v for k, v in seq_map.items()}
    return out


# ---------------------------------------------------------------------------
# The entity file
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> Optional[dict]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _collection(data: dict, name: str) -> List[dict]:
    try:
        from entities_io import entities_collection
        rows = entities_collection(data, name)
    except Exception:  # noqa: BLE001
        inner = data.get("entities") if isinstance(data.get("entities"), dict) else data
        rows = inner.get(name) if isinstance(inner, dict) else None
    return [r for r in (rows or []) if isinstance(r, dict)]


def _record_body(rec: dict) -> str:
    body = {k: v for k, v in rec.items() if k not in _VOLATILE_RECORD_KEYS}
    try:
        return json.dumps(body, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return repr(body)


def plan_entities(live_path, copy_path) -> Dict[str, Any]:
    """`{new: {collection: [records]}, differs: {collection: [ids]}, unreadable}`."""
    live = _load_json(Path(live_path))
    copy = _load_json(Path(copy_path))
    out: Dict[str, Any] = {"new": {}, "differs": {}, "unreadable": False,
                           "n_new": 0, "n_differs": 0}
    if live is None or copy is None:
        out["unreadable"] = True
        return out
    for name in ("people", "orgs", "threads"):
        live_by_id = {r.get("id"): r for r in _collection(live, name) if r.get("id")}
        for rec in _collection(copy, name):
            rid = rec.get("id")
            if not rid:
                continue
            if rid not in live_by_id:
                out["new"].setdefault(name, []).append(rec)
                out["n_new"] += 1
            elif _record_body(live_by_id[rid]) != _record_body(rec):
                out["differs"].setdefault(name, []).append(rid)
                out["n_differs"] += 1
    return out


def merge_entities(workspace_root, copy_path, *, apply: bool) -> Dict[str, Any]:
    """Create the records only the copy holds through the canonical writers;
    list the ones that differ on both sides for review. Never copies the
    file."""
    root = Path(workspace_root)
    live = root / "_hq" / "data" / "entities.json"
    plan = plan_entities(live, Path(copy_path))
    out = {"copy": Path(copy_path).name, "n_new": plan["n_new"],
           "n_differs": plan["n_differs"], "unreadable": plan["unreadable"],
           "created": [], "review": [], "id_map": {}}
    for name, ids in plan["differs"].items():
        for rid in ids:
            out["review"].append({"collection": name, "id": rid,
                                  "reason": "fields differ on both computers"})
    if plan["unreadable"] or not apply:
        for name, recs in plan["new"].items():
            for rec in recs:
                out["review"].append({"collection": name, "id": rec.get("id"),
                                      "reason": "only the copy holds it"})
        return out
    for name, recs in plan["new"].items():
        for rec in recs:
            rid = rec.get("id")
            try:
                if name == "threads":
                    from thread_writer import UNAFFILIATED_ORG_ID, create_thread
                    created = create_thread(
                        root, canonical_name=str(rec.get("canonical_name") or rid),
                        status=str(rec.get("status") or "active"),
                        folder_name=rec.get("folder_name"), kind=rec.get("kind"),
                        org_id=rec.get("org_id") or rec.get("affiliation_id")
                        or UNAFFILIATED_ORG_ID,
                        owner_person_id=rec.get("owner_person_id"),
                        parent_thread_id=rec.get("parent_thread_id"),
                        first_seen=rec.get("first_seen"), thread_id=rid,
                        source_skill=SOURCE_SKILL)
                    out["created"].append({"collection": name, "id": rid})
                    out["id_map"][rid] = (created.get("id") if isinstance(created, dict) else rid) or rid
                elif name == "people":
                    from people_writer import create_person
                    created = create_person(
                        root, canonical_name=str(rec.get("canonical_name") or rid),
                        primary_org_id=rec.get("primary_org_id"),
                        email=rec.get("email"), role=rec.get("role"),
                        notes=rec.get("notes"), aliases=rec.get("aliases"),
                        first_seen=rec.get("first_seen"),
                        source_skill=SOURCE_SKILL)
                    new_id = created.get("id") if isinstance(created, dict) else None
                    out["created"].append({"collection": name, "id": rid,
                                           "created_as": new_id})
                    out["id_map"][rid] = new_id or rid
                else:
                    out["review"].append({"collection": name, "id": rid,
                                          "reason": "no writer creates this kind here"})
            except Exception as exc:  # noqa: BLE001 - the writer's refusal is the answer
                out["review"].append({"collection": name, "id": rid,
                                      "reason": f"{type(exc).__name__}: {exc}"[:160]})
    return out


# ---------------------------------------------------------------------------
# Text files
# ---------------------------------------------------------------------------

def _strip_live_state(text: str) -> str:
    return _LIVE_STATE_RE.sub("", text or "")


def plan_text(live_path, copy_path) -> Dict[str, Any]:
    """The lines only the copy holds, outside LIVE-STATE blocks, as runs of
    consecutive lines: `{runs: [[line, ...]], n_lines}`."""
    try:
        live = Path(live_path).read_text(encoding="utf-8", errors="replace")
        copy = Path(copy_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"runs": [], "n_lines": 0, "unreadable": True}
    have = {ln.strip() for ln in _strip_live_state(live).splitlines() if ln.strip()}
    runs: List[List[str]] = []
    current: List[str] = []
    for ln in _strip_live_state(copy).splitlines():
        if ln.strip() and ln.strip() not in have:
            current.append(ln.rstrip())
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    return {"runs": runs, "n_lines": sum(len(r) for r in runs), "unreadable": False}


def merge_text(live_path, copy_path, *, apply: bool, today: Optional[str] = None) -> Dict[str, Any]:
    plan = plan_text(live_path, copy_path)
    out = {"copy": Path(copy_path).name, "n_lines": plan["n_lines"],
           "appended": False, "unreadable": plan.get("unreadable", False)}
    if not apply or not plan["runs"] or plan.get("unreadable"):
        return out
    from atomic_write import atomic_write_text

    live = Path(live_path)
    text = live.read_text(encoding="utf-8", errors="replace")
    stamp = today or _dt.date.today().isoformat()
    block = (f"\n\n## Recovered from {Path(copy_path).name} on {stamp}\n\n"
             + "\n\n".join("\n".join(run) for run in plan["runs"]) + "\n")
    atomic_write_text(live, text.rstrip("\n") + block)
    out["appended"] = True
    return out


# ---------------------------------------------------------------------------
# Archive
# ---------------------------------------------------------------------------

def archive_dir(workspace_root, today: Optional[str] = None) -> Path:
    stamp = today or _dt.date.today().isoformat()
    return Path(workspace_root) / "_archive" / f"cleanup-{stamp}" / "sync-conflict-copies"


def archive_copy(workspace_root, copy_path, note: str, *, today: Optional[str] = None) -> Dict[str, Any]:
    """Move one copy into the dated archive and add a manifest line. Never
    deletes: a mount that refuses the move gets the mv-aside idiom."""
    root = Path(workspace_root)
    copy = Path(copy_path)
    dest_dir = archive_dir(root, today)
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        rel = copy.relative_to(root)
        archived_name = "__".join(rel.parts)
    except ValueError:
        rel = copy
        archived_name = copy.name
    dest = dest_dir / archived_name
    moved_to = None
    try:
        shutil.move(str(copy), str(dest))
        moved_to = str(dest)
    except OSError:
        try:
            aside = copy.with_name(copy.name + f".archived.{int(_dt.datetime.now().timestamp())}")
            copy.rename(aside)
            moved_to = str(aside)
        except OSError:
            moved_to = None
    manifest = dest_dir / "MANIFEST.md"
    line = (f"| {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"| `{rel.as_posix() if isinstance(rel, Path) else rel}` "
            f"| `{Path(moved_to).name if moved_to else 'left in place'}` | {note} |\n")
    try:
        if not manifest.exists():
            manifest.write_text(
                "# Sync conflict copies\n\nCopies a sync service kept beside the "
                "live files, merged back by `sync_conflict_copies.py` and moved "
                "here. Nothing was deleted.\n\n| When | From | Archived as | Done |\n"
                "|---|---|---|---|\n", encoding="utf-8")
        with manifest.open("a", encoding="utf-8") as fh:
            fh.write(line)
    except OSError:
        pass
    return {"copy": str(copy), "moved_to": moved_to, "manifest": str(manifest)}


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------

def brief_line(result: Dict[str, Any]) -> str:
    """The one sentence the brief says, or '' when nothing happened."""
    n = int(result.get("n_recovered") or 0)
    held = result.get("held") or []
    review = int(result.get("n_review") or 0)
    parts = []
    if n:
        parts.append(f"Recovered {n} {'entry' if n == 1 else 'entries'} a second "
                     f"computer wrote while out of sync.")
    if review:
        parts.append(f"{review} record{'s' if review != 1 else ''} differ between "
                     f"the two computers and wait for your review.")
    if held:
        parts.append("A second computer's copy of your activity log holds more "
                     "than this one; nothing was merged until you say which "
                     "computer is current.")
    return " ".join(parts)


def run(workspace_root, *, apply: bool = True, fired_via: Optional[str] = None,
        triggered_by: Optional[str] = None, now_iso: Optional[str] = None) -> Dict[str, Any]:
    """Find, merge, archive, receipt. Returns the receipt payload."""
    root = Path(workspace_root)
    found = scan(root)
    today = (now_iso or "")[:10] or None
    result: Dict[str, Any] = {
        "leg": JOB_ID, "n_found": found["n"], "n_recovered": 0, "n_review": 0,
        "n_text_lines": 0, "events": [], "entities": [], "text": [],
        "archived": [], "held": [], "refused": [], "apply": bool(apply),
        "n_receipts_skipped": 0, "reference_drift": False}
    held_tags: set = set()
    for item in found["copies"]:
        copy = Path(item["copy"])
        kind = item["kind"]
        note = ""
        if kind == "events":
            r = merge_events(root, copy, apply=apply)
            result["events"].append(r)
            if r.get("held"):
                result["held"].append({"copy": copy.name, "reason": r["held"],
                                       "n_missing": r.get("n_missing")})
                held_tags.add(item.get("tag"))
                continue  # the copy stays; this is a question, not a merge
            result["n_recovered"] += int(r.get("n_recovered") or 0)
            result["n_receipts_skipped"] += int(r.get("n_receipts_skipped") or 0)
            result["reference_drift"] = result["reference_drift"] or bool(r.get("reference_drift"))
            result["refused"].extend(r.get("refused") or [])
            note = (f"{r.get('n_recovered', 0)} of {r.get('n_missing', 0)} entries "
                    f"appended with fresh numbers")
        elif kind == "entities":
            r = merge_entities(root, copy, apply=apply)
            result["entities"].append(r)
            result["n_review"] += len(r.get("review") or [])
            note = (f"{len(r.get('created') or [])} created through the writers, "
                    f"{len(r.get('review') or [])} for review")
        elif kind == "aliases":
            result["n_review"] += 1
            note = "aliases copy kept for review"
        elif kind == "sidecar":
            if item.get("tag") in held_tags:
                continue  # its log copy stays, so its sidecar stays beside it
            note = "sidecar of a conflict copy"
        else:
            r = merge_text(item["live"], copy, apply=apply, today=today)
            result["text"].append(r)
            result["n_text_lines"] += int(r.get("n_lines") or 0)
            note = f"{r.get('n_lines', 0)} lines appended" if r.get("appended") else "nothing new"
        if apply:
            result["archived"].append(archive_copy(root, copy, note, today=today))
    result["line"] = brief_line(result)
    result["status"] = ("held" if result["held"] else "complete")
    if apply and (found["n"] or result["held"]):
        _receipt(root, result, fired_via=fired_via, triggered_by=triggered_by)
    return result


def _receipt(root: Path, result: Dict[str, Any], *, fired_via, triggered_by) -> None:
    try:
        from receipts import effective_fired_via, log_receipt
    except Exception:  # noqa: BLE001
        return
    extra = {k: result[k] for k in ("n_found", "n_recovered", "n_review",
                                    "n_text_lines", "line", "held", "refused",
                                    "n_receipts_skipped", "reference_drift")}
    extra["archived"] = [Path(a["moved_to"]).name for a in result["archived"] if a.get("moved_to")]
    try:
        via = effective_fired_via(fired_via)
    except Exception:  # noqa: BLE001
        via = fired_via or "manual"
    try:
        log_receipt(root, JOB_ID, receipt_type=RECEIPT_TYPE,
                    status=result["status"], fired_via=via,
                    triggered_by=triggered_by, extra_data=extra)
        result["receipt_written"] = True
    except Exception as exc:  # noqa: BLE001
        result["receipt_written"] = False
        result["receipt_error"] = f"{type(exc).__name__}: {exc}"[:200]


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="merge sync conflict copies of the book")
    ap.add_argument("--workspace", required=True)
    ap.add_argument("--apply", action="store_true", help="merge and archive; without it, report only")
    ap.add_argument("--fired-via", default=None)
    ap.add_argument("--triggered-by", default=None)
    args = ap.parse_args(argv)
    if not args.apply:
        print(json.dumps(scan(args.workspace), default=str))
        return 0
    res = run(args.workspace, apply=True, fired_via=args.fired_via,
              triggered_by=args.triggered_by)
    print(json.dumps(res, default=str))
    return 0


__all__ = ["JOB_ID", "RECEIPT_TYPE", "HELD_LIVE_STALE", "copy_tag",
           "conflict_copies_of", "scan", "plan_events", "merge_events",
           "plan_entities", "merge_entities", "plan_text", "merge_text",
           "archive_copy", "brief_line", "run"]


if __name__ == "__main__":
    sys.exit(main())
