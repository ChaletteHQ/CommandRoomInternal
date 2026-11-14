#!/usr/bin/env python3
"""Which log file this computer appends to (cr1#1061 slice 2, parent #790).

A seat used from two computers through a sync service must never have two
writers on one file. Each computer therefore appends to its own log,
`_hq/data/computers/<mark>/events.jsonl`, and every reader sees the merged
view (`events_io.log_streams` / `merge_streams`, slice 1). The mark is the
lasting per-computer mark (`computer_mark`, owner ruling 1a, cr1#922).

ONE COMPUTER STAYS BYTE-IDENTICAL. The live `events.jsonl` is never moved
(ruling A2) and keeps ONE owner: of the computers this one knows about, the
one whose mark sorts first, unless the owner pinned one by hand (slice 3,
`--own-live-log`, see THE SWITCH below). A computer that knows of no other writes the live
file exactly as today and creates nothing. Only a computer that knows of
another, and is not the owner, writes its own file.

HOW A COMPUTER KNOWS OF ANOTHER (single-writer evidence only):
  * a sub-folder of `computers/` named for another mark (each computer only
    ever creates its own), and
  * the `computer` field of the writer-lock stamp `.writer.lock.info`, and of
    any sync-service copy of it (`.writer.lock-<device>.info`, #922 saw one).
When a computer first learns of another it records that it exists too, by
creating its own folder (`computers/<mark>/present.json`), so the other side
learns of it even after the lock stamp is overwritten. Both sides then agree
on the owner, and the one that is not the owner moves to its own file. Until
the folders have synced, both may still write the live file: that is today's
behaviour, never worse.

NUMBERS (owner ruling 2a, 2026-11-11). A computer's own file continues the
merged numbering: the next number is one above the highest number in ANY log
(the live file and its high-water, and every computer's file). Two computers
out of sync can still hand out one number twice; `events_io.merge_report`
lists it and system health names it. Nothing resolves it automatically.

STAMPS. Each entry in a per-computer file carries `computer` (the mark),
`computer_count` (that computer's own count, 1, 2, 3 ...) and `fp` (the
content fingerprint, `events_io.fingerprint`, over everything else). Entries
in the live file are unchanged.

`CR_ONE_LOG_PER_COMPUTER=0` turns the routing off (every write goes to the
live file, as before). Stdlib only, 3.10-safe. Never raises.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Dict, Optional, Set

COMPUTERS_DIRNAME = "computers"
PRESENT_NAME = "present.json"
SWITCH_ENV = "CR_ONE_LOG_PER_COMPUTER"
COUNT_FIELD = "computer_count"
COMPUTER_FIELD = "computer"

_LOCK_INFO_RE = re.compile(r"^\.writer\.lock.*\.info$")

#: Evidence of another computer expires: a renamed or retired computer's
#: folder, or a lock stamp (or a sync copy of one) naming a mark, counts only
#: while something there is younger than this. A computer that knows of
#: another refreshes its own folder's `present.json` at most once a day, so a
#: computer still in use never ages out (review round, cr1#1061).
EVIDENCE_MAX_AGE_S = 30 * 86400
PRESENCE_REFRESH_S = 86400


def _mark_module():
    try:
        import computer_mark  # type: ignore
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        import computer_mark  # type: ignore
    return computer_mark


KNOWS_FIELD = "knows"


def _direct_marks(data_dir: Path, now: float) -> Set[str]:
    """Marks shown DIRECTLY: a fresh sub-folder of `computers/`, or a fresh
    lock stamp (or sync copy of one) naming a mark."""
    out: Set[str] = set()
    cm = _mark_module()
    base = Path(data_dir) / COMPUTERS_DIRNAME
    try:
        if base.is_dir():
            for d in base.iterdir():
                if d.is_dir() and cm.is_mark(d.name) and _fresh_dir(d, now):
                    out.add(d.name)
    except OSError:
        pass
    try:
        for p in Path(data_dir).iterdir():
            if not _LOCK_INFO_RE.match(p.name):
                continue
            try:
                info = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            mark = info.get(COMPUTER_FIELD) if isinstance(info, dict) else None
            if cm.is_mark(mark or "") and _fresh_stamp(p, info, now):
                out.add(mark)
    except OSError:
        pass
    return out


def _read_present(own_dir: Path) -> dict:
    try:
        raw = json.loads((own_dir / PRESENT_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _remembered_marks(data_dir: Path, now: float) -> Set[str]:
    """Marks a computer recorded as seen directly within the age limit, in
    its own `present.json` (`knows`: mark -> when last seen directly). A
    stamp is overwritten by the next locker; this record is what keeps a
    computer known after that."""
    out: Set[str] = set()
    cm = _mark_module()
    base = Path(data_dir) / COMPUTERS_DIRNAME
    try:
        dirs = [d for d in base.iterdir() if d.is_dir()] if base.is_dir() else []
    except OSError:
        return out
    for d in dirs:
        knows = _read_present(d).get(KNOWS_FIELD)
        if not isinstance(knows, dict):
            continue
        for mark, seen in knows.items():
            if (cm.is_mark(mark) and isinstance(seen, (int, float))
                    and now - seen <= EVIDENCE_MAX_AGE_S):
                out.add(mark)
    return out


def known_marks(data_dir: Path) -> Set[str]:
    """Every computer mark this folder shows evidence of. Never raises."""
    import time
    now = time.time()
    try:
        return _direct_marks(data_dir, now) | _remembered_marks(data_dir, now)
    except Exception:  # noqa: BLE001
        return set()


def _fresh_dir(d: Path, now: float) -> bool:
    try:
        newest = max((f.stat().st_mtime for f in d.iterdir() if f.is_file()),
                     default=0.0)
    except OSError:
        return False
    return now - newest <= EVIDENCE_MAX_AGE_S


def _fresh_stamp(p: Path, info: dict, now: float) -> bool:
    when = None
    raw = info.get("acquired_at")
    if isinstance(raw, str) and raw:
        try:
            import datetime as _dt
            dt = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_dt.timezone.utc)
            when = dt.timestamp()
        except ValueError:
            when = None
    if when is None:
        try:
            when = p.stat().st_mtime
        except OSError:
            return False
    return now - when <= EVIDENCE_MAX_AGE_S


def _is_live_log(path: Path) -> bool:
    """The live `_hq/data/events.jsonl` (not a per-computer file, not a
    shard, not some other `events.jsonl` a caller names)."""
    return (path.name == "events.jsonl"
            and path.parent.parent.name != COMPUTERS_DIRNAME)


def route(path, env: Optional[Dict[str, str]] = None) -> Optional[dict]:
    """Where an append to `path` should land, or None to write `path` as
    today. A dict means: write `target` (this computer's own file) and stamp
    `mark`. Records this computer's presence when it first learns of
    another. Never raises."""
    try:
        e = dict(os.environ) if env is None else dict(env)
        if str(e.get(SWITCH_ENV, "1")).strip() == "0":
            return None
        path = Path(path)
        if not _is_live_log(path):
            return None
        data_dir = path.parent
        if routing_off(data_dir):
            _acknowledge_off(data_dir, e)
            return None   # the undo switched it off for every computer
        mark = _mark_module().computer_mark(e)
        if not mark:
            return None
        import time
        now = time.time()
        direct = _direct_marks(data_dir, now) - {mark}
        others = direct | (_remembered_marks(data_dir, now) - {mark})
        if not others:
            return None
        own_dir = data_dir / COMPUTERS_DIRNAME / mark
        _record_presence(own_dir, mark, direct, now)
        if owner_of(data_dir, others | {mark}) == mark:
            return None   # this computer owns the live file (pinned or first)
        return {"target": own_dir / "events.jsonl", "mark": mark,
                "live": path}
    except Exception:  # noqa: BLE001 - routing never blocks a write
        return None


def _record_presence(own_dir: Path, mark: str, direct: Set[str],
                     now: float) -> None:
    """This computer's own record (single writer): its mark, and every other
    computer it has seen DIRECTLY with when. Rewritten when it learns a new
    mark, and at most once a day otherwise (which keeps its folder fresh)."""
    present = own_dir / PRESENT_NAME
    try:
        prior = _read_present(own_dir)
        knows = prior.get(KNOWS_FIELD) if isinstance(prior.get(KNOWS_FIELD), dict) else {}
        new_mark = any(m not in knows for m in direct)
        try:
            age = now - present.stat().st_mtime
        except OSError:
            age = None
        if age is not None and age < PRESENCE_REFRESH_S and not new_mark:
            return
        merged = {m: s for m, s in knows.items()
                  if isinstance(s, (int, float)) and now - s <= EVIDENCE_MAX_AGE_S}
        for m in direct:
            merged[m] = int(now)
        own_dir.mkdir(parents=True, exist_ok=True)
        tmp = present.with_name(present.name + ".tmp")
        tmp.write_text(json.dumps({COMPUTER_FIELD: mark,
                                   KNOWS_FIELD: dict(sorted(merged.items()))})
                       + chr(10), encoding="utf-8")
        os.replace(tmp, present)
    except OSError:
        pass


def _tail_max(path: Path, field: str = "seq") -> int:
    """The highest integer `field` in the last 64 KiB of a log, or 0."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > 65536:
                fh.seek(size - 65536)
                fh.readline()   # drop the partial first line
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return 0
    best = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        v = ev.get(field) if isinstance(ev, dict) else None
        if isinstance(v, int) and not isinstance(v, bool) and 0 < v < 10 ** 10:
            best = max(best, v)
    return best


def _seqhw(path: Path) -> int:
    try:
        raw = json.loads(path.with_name(path.name + ".seqhw").read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    v = raw.get("max_seq") if isinstance(raw, dict) else raw
    return v if isinstance(v, int) and not isinstance(v, bool) else 0


def merged_seq_floor(live: Path, own: Path) -> int:
    """The highest number in every OTHER log of the merged view: the live
    file (and its high-water) and every other computer's file. The own file's
    maximum is read by the appender itself, under its lock."""
    floor = max(_tail_max(live), _seqhw(live))
    base = live.parent / COMPUTERS_DIRNAME
    try:
        for d in base.iterdir() if base.is_dir() else ():
            f = d / "events.jsonl"
            if d.is_dir() and f != own and f.exists():
                floor = max(floor, _tail_max(f), _seqhw(f))
    except OSError:
        pass
    return floor


def computer_files_floor(live) -> int:
    """The highest number in every computer's own file beside the live log,
    or 0 when there is none (every seat with one computer). Never raises."""
    try:
        live = Path(live)
        if not _is_live_log(live):
            return 0
        base = live.parent / COMPUTERS_DIRNAME
        if not base.is_dir():
            return 0
        floor = 0
        for d in base.iterdir():
            f = d / "events.jsonl"
            if d.is_dir() and f.exists():
                floor = max(floor, _tail_max(f), _seqhw(f))
        return floor
    except Exception:  # noqa: BLE001
        return 0


def own_count(tail_text: str) -> int:
    """The highest `computer_count` in a tail of this computer's own file."""
    best = 0
    for line in (tail_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        v = ev.get(COUNT_FIELD) if isinstance(ev, dict) else None
        if isinstance(v, int) and not isinstance(v, bool):
            best = max(best, v)
    return best


def stamp(evs: list, mark: str, start_count: int) -> list:
    """Stamp each entry with this computer, its own count and the content
    fingerprint (computed last, over everything else)."""
    try:
        from events_io import fingerprint
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from events_io import fingerprint
    n = start_count
    for ev in evs:
        n += 1
        ev[COMPUTER_FIELD] = mark
        ev[COUNT_FIELD] = n
        ev.pop("fp", None)
        ev["fp"] = fingerprint(ev)
    return evs


# ---------------------------------------------------------------------------
# Slice 3 (cr1#1061): the owner's switch, the undo, and the health line.
# ---------------------------------------------------------------------------
#
# THE SWITCH (owner ruling 2026-11-12). The mark that sorts first owns the live
# log. The owner can change that once, by hand, by running
# `computer_logs.py --workspace <ws> --own-live-log` ON the computer that
# should own it. That writes the pin, `computers/owner.json`, which only this
# command writes. Every computer reads it in `route`: the pinned computer
# writes the live file, every other one its own. A pin naming a computer with
# no fresh evidence (retired, renamed, unseen for 30 days) lapses, and the
# sort rule applies again, so a pin can never leave a seat with no owner.
#
# THE UNDO. `--undo` turns the routing off for every computer (the file
# `computers/routing_off.json`, which `route` reads before anything else; the
# env switch stays for one process), then folds each computer's rows back into
# the live log through the one append path, the way cr1#724 folds a sync
# conflict copy back: only rows the live log does not already hold (by their
# provenance, `data.recovered_from`, else by content), in their original
# order, with fresh numbers and their original time, each row carrying
# `data.recovered_from` = {computer, seq, computer_count}. A number reference
# (any `*_seq` field, at the top of a row or in its data) between two rows of
# the same computer is renumbered with them. Run records stay out, as in
# cr1#724. A folder folds ALL OR NOTHING: the gate sees every row first, so a
# row this computer's plugin would refuse folds none of them; and the fold is
# HELD when an entry elsewhere points at a number only that folder holds,
# because the folded row's fresh number would leave that pointer leading
# nowhere. A held or refused folder stays where it is and is still read.
# Each folded folder moves to `_archive/cleanup-<date>/computer-logs/<mark>/`
# with a MANIFEST.md. Nothing is deleted. Another computer's folder waits
# until that computer has acknowledged the off switch (its next append writes
# `computers/off_seen-<mark>.json`) and its log has been quiet for
# `UNDO_QUIET_S` seconds, or until it has not been seen for 30 days. A re-run
# folds only what is new, and writes a receipt only when it folded something.
# `--routing-on` turns routing back on (the off file and the acknowledgements
# are archived, not deleted).

PIN_NAME = "owner.json"
OFF_NAME = "routing_off.json"
OWNER_FIELD = "owner"
UNDO_QUIET_S = 600
UNDO_HOLDER = "computer-logs-undo"
SWITCH_RECEIPT = "computer_log_owner_set"
UNDO_RECEIPT = "computer_logs_folded"
ACK_PREFIX = "off_seen-"


def _computers_dir(data_dir: Path) -> Path:
    return Path(data_dir) / COMPUTERS_DIRNAME


def routing_off(data_dir: Path) -> bool:
    """True when the undo has turned per-computer routing off for the seat."""
    try:
        return (_computers_dir(data_dir) / OFF_NAME).is_file()
    except OSError:
        return False


def pinned_owner(data_dir: Path) -> Optional[str]:
    """The mark the owner pinned as the live log's owner, or None."""
    try:
        raw = json.loads((_computers_dir(data_dir) / PIN_NAME).read_text(
            encoding="utf-8"))
    except (OSError, ValueError):
        return None
    mark = raw.get(OWNER_FIELD) if isinstance(raw, dict) else None
    return mark if _mark_module().is_mark(mark or "") else None


def owner_of(data_dir: Path, marks: Set[str]) -> Optional[str]:
    """Who owns the live log among `marks` (every computer known, this one
    included): the pinned mark while it is among them, else the one that
    sorts first."""
    if not marks:
        return None
    pin = pinned_owner(data_dir)
    if pin and pin in marks:
        return pin
    return min(marks)


def _write_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(body, sort_keys=True) + chr(10), encoding="utf-8")
    os.replace(tmp, path)


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _receipt(live: Path, rtype: str, data: dict) -> None:
    """One plain receipt row on the live log; never blocks the action."""
    try:
        from event_gate import append_event
        append_event(live, [{"type": rtype, "source_skill": UNDO_HOLDER,
                             "data": data}], holder=UNDO_HOLDER)
    except Exception:  # noqa: BLE001
        pass


def set_owner(workspace_root, env: Optional[Dict[str, str]] = None) -> dict:
    """Pin THIS computer as the live log's owner (the owner's one-time switch).
    `{ok, owner, previous}` or `{ok: False, reason}`."""
    data_dir = Path(workspace_root) / "_hq" / "data"
    if not (data_dir / "events.jsonl").exists():
        return {"ok": False, "reason": "no_log"}
    mark = _mark_module().computer_mark(
        dict(os.environ) if env is None else dict(env))
    if not mark:
        return {"ok": False, "reason": "no_mark"}
    previous = owner_of(data_dir, known_marks(data_dir) | {mark})
    try:
        _write_json(_computers_dir(data_dir) / PIN_NAME,
                    {OWNER_FIELD: mark, "set_at": _now_iso()})
    except OSError as exc:
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}"}
    _receipt(data_dir / "events.jsonl", SWITCH_RECEIPT,
             {"owner": mark, "previous": previous})
    return {"ok": True, "owner": mark, "previous": previous}


def _ack_path(data_dir: Path, mark: str) -> Path:
    return _computers_dir(data_dir) / f"{ACK_PREFIX}{mark}.json"


def _acknowledge_off(data_dir: Path, e: Dict[str, str]) -> None:
    """A computer that finds routing switched off says so once, by a file of
    its own beside the folders (`computers/off_seen-<mark>.json`), so the undo
    on another computer knows it has stopped writing its own log before that
    log is folded and archived. Never raises."""
    try:
        mark = _mark_module().computer_mark(e)
        if not mark:
            return
        ack = _ack_path(data_dir, mark)
        if not ack.exists():
            _write_json(ack, {COMPUTER_FIELD: mark, "seen_at": _now_iso()})
    except Exception:  # noqa: BLE001
        pass


def set_routing(workspace_root, on: bool, today: Optional[str] = None) -> dict:
    """Turn per-computer routing off (write the off file) or back on (archive
    it and every computer's acknowledgement of it). Idempotent."""
    data_dir = Path(workspace_root) / "_hq" / "data"
    base = _computers_dir(data_dir)
    off = base / OFF_NAME
    try:
        if not on:
            if not off.is_file():
                _write_json(off, {"off_at": _now_iso()})
            return {"ok": True, "routing": "off"}
        if off.is_file():
            import shutil
            dest_dir = _archive_root(workspace_root, today)
            dest_dir.mkdir(parents=True, exist_ok=True)
            for p in [off] + sorted(base.glob(f"{ACK_PREFIX}*.json")):
                shutil.move(str(p), str(_free_name(dest_dir / p.name)))
        return {"ok": True, "routing": "on"}
    except OSError as exc:
        return {"ok": False, "reason": f"write_failed: {type(exc).__name__}"}


def _archive_root(workspace_root, today: Optional[str] = None) -> Path:
    import datetime as _dt
    day = today or _dt.date.today().isoformat()
    return Path(workspace_root) / "_archive" / f"cleanup-{day}" / "computer-logs"


def _free_name(dest: Path) -> Path:
    if not dest.exists():
        return dest
    n = 2
    while True:
        cand = dest.with_name(f"{dest.name}-{n}")
        if not cand.exists():
            return cand
        n += 1


def _strip_stamps(ev: dict) -> dict:
    return {k: v for k, v in ev.items()
            if k not in (COMPUTER_FIELD, COUNT_FIELD, "fp", "seq")}


def _int(v) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def _refs(ev: dict):
    """Every entry number a row points at: any whole-number field whose name
    ends in `_seq`, at the top of the row or in its `data` (`supersedes_seq`,
    `commitment_seq`, `meeting_seq` ...). Yields (where, key, number)."""
    for where, d in (("top", ev), ("data", ev.get("data"))):
        if not isinstance(d, dict):
            continue
        for k, v in d.items():
            if k != "seq" and k.endswith("_seq") and _int(v) is not None:
                yield where, k, v


def _live_rows(workspace_root: Path) -> list:
    """Every row of the live log, its yearly shards included."""
    import sync_conflict_copies as scc
    try:
        import events_io
        paths = events_io.shard_paths(workspace_root)
    except Exception:  # noqa: BLE001
        paths = [Path(workspace_root) / "_hq" / "data" / "events.jsonl"]
    rows = []
    for p in paths:
        rows.extend(scc._parse_jsonl(p))
    return rows


def _recovered_from(ev: dict, mark: str) -> bool:
    d = ev.get("data")
    rf = d.get("recovered_from") if isinstance(d, dict) else None
    return isinstance(rf, dict) and rf.get("computer") == mark


def _plan_fold(workspace_root: Path, folder: Path) -> dict:
    """What folding one computer's folder would do (see THE UNDO). Pure read.

    A row is already folded when a live row's `data.recovered_from` names this
    computer and its number (so a re-run, or a re-run after a failure part way,
    never folds a row twice), and already there when the live log holds the
    same content. The fold is HELD, and nothing of this folder is folded, when
    a row anywhere else (the live log, or another computer's folder) points at
    a number only this folder holds: the folded row gets a fresh number, so
    that pointer would lead nowhere once the folder is archived."""
    import sync_conflict_copies as scc
    mark = folder.name
    rows = scc._parse_jsonl(folder / "events.jsonl")
    live = _live_rows(workspace_root)
    done: Dict[int, int] = {}
    sig_seq: dict = {}
    live_nums = set()
    for e in live:
        n = scc._seq_of(e)
        if n is not None:
            live_nums.add(n)
        if _recovered_from(e, mark) and n is not None:
            done[e["data"]["recovered_from"].get("seq")] = n
        sig_seq.setdefault(scc._sig(_strip_stamps(e)), n)
    plan = {"computer": mark, "n_rows": len(rows), "missing": [],
            "seq_map": {}, "n_already": 0, "n_run_records_left": 0,
            "n_outside_refs": 0}
    seen = set()
    for ev in rows:
        old = scc._seq_of(ev)
        if old is not None and old in done:
            plan["seq_map"][old] = done[old]
            plan["n_already"] += 1
            continue
        s = scc._sig(_strip_stamps(ev))
        if s in sig_seq:
            if old is not None and sig_seq[s] is not None:
                plan["seq_map"][old] = sig_seq[s]
            plan["n_already"] += 1
            continue
        if s in seen:
            continue
        seen.add(s)
        if scc._is_run_record(ev):
            plan["n_run_records_left"] += 1
            continue
        plan["missing"].append(ev)
    only_here = {scc._seq_of(e) for e in plan["missing"]} - live_nums - {None}
    if only_here:
        outside = [e for e in live if not _recovered_from(e, mark)]
        try:
            for d in sorted(folder.parent.iterdir()):
                if d.is_dir() and d != folder and (d / "events.jsonl").is_file():
                    outside.extend(scc._parse_jsonl(d / "events.jsonl"))
        except OSError:
            pass
        plan["n_outside_refs"] = sum(1 for e in outside for _w, _k, n in _refs(e)
                                     if n in only_here)
    return plan


def _fold_one(workspace_root: Path, folder: Path, apply: bool) -> dict:
    """Fold one computer's rows into the live log, all or nothing (see THE
    UNDO and `_plan_fold`)."""
    import sync_conflict_copies as scc
    plan = _plan_fold(workspace_root, folder)
    missing = plan["missing"]
    out = {"computer": plan["computer"], "n_rows": plan["n_rows"],
           "n_missing": len(missing), "n_already": plan["n_already"],
           "n_run_records_left": plan["n_run_records_left"],
           "n_folded": 0, "refused": [], "held": None}
    if plan["n_outside_refs"]:
        n = plan["n_outside_refs"]
        out["held"] = (f"{n} {'entry' if n == 1 else 'entries'} elsewhere "
                       f"point{'s' if n == 1 else ''} at entries only this "
                       "computer's own log holds")
        return out
    if not apply or not missing:
        return out
    live = Path(workspace_root) / "_hq" / "data" / "events.jsonl"
    seq_map: Dict[int, int] = dict(plan["seq_map"])

    def shaped(ev: dict) -> dict:
        row = _strip_stamps(ev)
        data = dict(row["data"])
        for where, k, n in list(_refs(ev)):
            if n in seq_map:
                if where == "top":
                    row[k] = seq_map[n]
                else:
                    data[k] = seq_map[n]
        data["recovered_from"] = {"computer": plan["computer"],
                                  "seq": scc._seq_of(ev),
                                  "computer_count": ev.get(COUNT_FIELD)}
        data["recovered_by"] = UNDO_HOLDER
        row["data"] = data
        return row

    bad = [scc._seq_of(e) for e in missing if not isinstance(e.get("data"), dict)]
    if bad:
        out["refused"] = [{"seq": n, "reason": "data is not an object"} for n in bad]
        return out
    # Preflight: the gate sees every row before one is written, so a row this
    # computer's plugin would refuse (another version's event type) folds
    # nothing rather than half the folder.
    try:
        from event_gate import append_event, gate_events
        gate_events([shaped(e) for e in missing], strict_enum=True,
                    holder=UNDO_HOLDER, events_jsonl_path=live)
    except Exception as exc:  # noqa: BLE001
        out["refused"] = [{"seq": None,
                           "reason": f"{type(exc).__name__}: {exc}"[:300]}]
        return out
    for ev in missing:
        old = scc._seq_of(ev)
        try:
            got = append_event(live, [shaped(ev)], holder=UNDO_HOLDER)
            reason = "not_written"
        except Exception as exc:  # noqa: BLE001
            got, reason = None, f"{type(exc).__name__}: {exc}"[:200]
        if not got:
            # Stop here: the folder stays, and a re-run continues from this
            # row (rows already folded are known by their provenance).
            out["refused"].append({"seq": old, "reason": reason})
            return out
        out["n_folded"] += len(got)
        if old is not None and scc._seq_of(got[0]) is not None:
            seq_map[old] = scc._seq_of(got[0])
    return out


def _stale_folder(folder: Path, now: float) -> bool:
    """No file in the folder is younger than the evidence window: a computer
    retired or unseen for 30 days, which will never acknowledge the off
    switch."""
    try:
        files = [p for p in folder.iterdir() if p.is_file()]
        return bool(files) and all(now - p.stat().st_mtime > EVIDENCE_MAX_AGE_S
                                   for p in files)
    except OSError:
        return False


def undo(workspace_root, *, apply: bool = True, today: Optional[str] = None,
         env: Optional[Dict[str, str]] = None, now: Optional[float] = None) -> dict:
    """Turn routing off and fold every computer's rows back into the live log
    (see THE UNDO). `apply=False` says what it would do and writes nothing.

    Another computer's folder is folded only once that computer has
    acknowledged the off switch (`off_seen-<mark>.json`, written by its next
    append) and its log has been quiet for `UNDO_QUIET_S`, or once it has not
    been seen for 30 days; until then it is `waiting` and a re-run picks it
    up. This computer's own folder never waits."""
    import time
    root = Path(workspace_root)
    data_dir = root / "_hq" / "data"
    if not (data_dir / "events.jsonl").exists():
        return {"ok": False, "reason": "no_log"}
    now = time.time() if now is None else now
    mine = _mark_module().computer_mark(
        dict(os.environ) if env is None else dict(env))
    result = {"ok": True, "apply": apply, "folded": [], "waiting": [],
              "held": [], "archived": []}
    if apply:
        r = set_routing(root, on=False, today=today)
        if not r.get("ok"):
            return r
    base = _computers_dir(data_dir)
    try:
        folders = sorted(d for d in base.iterdir()
                         if d.is_dir() and (d / "events.jsonl").is_file()) \
            if base.is_dir() else []
    except OSError:
        folders = []
    for d in folders:
        if d.name != mine and not _stale_folder(d, now):
            try:
                age = now - (d / "events.jsonl").stat().st_mtime
            except OSError:
                age = None
            if (not _ack_path(data_dir, d.name).is_file()
                    or (age is not None and age < UNDO_QUIET_S)):
                result["waiting"].append(d.name)
                continue
        got = _fold_one(root, d, apply)
        result["folded"].append(got)
        if got["held"]:
            result["held"].append(d.name)
        if apply and not got["refused"] and not got["held"]:
            dest = _free_name(_archive_root(root, today) / d.name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            import shutil
            shutil.move(str(d), str(dest))
            _manifest(dest.parent, dest.name, got)
            result["archived"].append(d.name)
    if apply and any(g["n_folded"] for g in result["folded"]):
        _receipt(data_dir / "events.jsonl", UNDO_RECEIPT, {
            "folded": {g["computer"]: g["n_folded"] for g in result["folded"]},
            "waiting": result["waiting"], "held": result["held"]})
    return result


def _manifest(arch: Path, name: str, got: dict) -> None:
    try:
        with open(arch / "MANIFEST.md", "a", encoding="utf-8") as fh:
            fh.write(f"- `{name}/`: this computer's own activity log, folded "
                     f"back into `_hq/data/events.jsonl` by the undo of "
                     f"cr1#1061 ({got['n_folded']} rows folded, "
                     f"{got['n_already']} already there, "
                     f"{got['n_run_records_left']} run records left here: "
                     f"that computer's own job history, no longer read). "
                     f"Nothing was deleted.\n")
    except OSError:
        pass


def _short(mark: str) -> str:
    return mark[:9] if mark else "?"


def health(workspace_root, env: Optional[Dict[str, str]] = None) -> dict:
    """What system health says about the computers writing this seat:
    `{computers, this, owner, pinned, routing_off, per_computer,
    seq_collisions, lines}`. `lines` is empty on a seat with one computer and
    no computer logs, so that seat's health check is unchanged. Read-only;
    never raises."""
    empty = {"computers": [], "this": None, "owner": None, "pinned": None,
             "routing_off": False, "per_computer": {}, "seq_collisions": [],
             "lines": []}
    try:
        data_dir = Path(workspace_root) / "_hq" / "data"
        mine = _mark_module().computer_mark(
            dict(os.environ) if env is None else dict(env))
        marks = set(known_marks(data_dir))
        off = routing_off(data_dir)
        try:
            import events_io
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            import events_io
        has_logs = events_io.has_computer_logs(workspace_root)
        # The lock stamp names this computer on every seat, so only ANOTHER
        # computer, a computer's own log or the off switch is news.
        if not (marks - {mine}) and not has_logs and not off:
            return dict(empty, this=mine)
        if mine:
            marks.add(mine)
        rep = events_io.merge_report(workspace_root)
        per = dict(rep.get("per_stream") or {})
        live_n = per.pop("events.jsonl", 0)
        owner = None if off else owner_of(data_dir, marks)
        pin = pinned_owner(data_dir)
        collisions = list(rep.get("seq_collisions") or [])
        out = {"computers": sorted(marks | set(per)), "this": mine,
               "owner": owner, "pinned": pin if pin == owner else None,
               "routing_off": off, "per_computer": per,
               "live_entries": live_n, "seq_collisions": collisions,
               "to_fold": {}, "lines": []}
        if off:
            # What the undo still has to fold, per computer: rows already
            # folded or already in the activity log do not count.
            for d in events_io.computer_log_dirs(workspace_root):
                plan = _plan_fold(Path(workspace_root), d)
                if plan["missing"]:
                    out["to_fold"][d.name] = {
                        "n": len(plan["missing"]),
                        "held": bool(plan["n_outside_refs"])}
        out["lines"] = _health_lines(out)
        return out
    except Exception:  # noqa: BLE001
        return empty


def _n(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _health_lines(h: dict) -> list:
    lines = []
    comps = h["computers"]
    per = h["per_computer"]
    me = h["this"]

    def who(m):
        return f"this computer ({_short(m)})" if m == me else _short(m)

    if h["routing_off"]:
        left = h.get("to_fold") or {}
        if left:
            parts = []
            for m, t in sorted(left.items()):
                part = f"{who(m)} still has {_n(t['n'], 'entry', 'entries')} to fold back"
                if t.get("held"):
                    part += (" (held: other entries point at them by number, "
                             "so they stay in its own log, where they are still read)")
                parts.append(part)
            lines.append("Each computer's own activity log is switched off, and "
                         + "; ".join(parts)
                         + ". Run the undo again to fold them into the activity log.")
        return lines
    if len(comps) < 2 and not per:
        return lines
    owner = h["owner"]
    head = (f"This seat writes from {_n(len(comps), 'computer', 'computers')}. "
            f"{who(owner).capitalize() if owner == me else who(owner)} owns the "
            f"activity log ({_n(h['live_entries'], 'entry', 'entries')}"
            f"{', set by hand' if h['pinned'] else ''})")
    rest = [f"{who(m)} keeps its own ({_n(per.get(m, 0), 'entry', 'entries')})"
            for m in comps if m != owner]
    lines.append(head + ("; " + "; ".join(rest) if rest else "") + ".")
    n = len(h["seq_collisions"])
    if n:
        lines.append(
            f"⚠ {_n(n, 'entry number was', 'entry numbers were')} given out on "
            "two computers while they were out of sync. Nothing was lost; an "
            "item that points at one of those numbers may point at either entry.")
    return lines


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=(
        "The computers writing this seat (cr1#1061): show them, pin this "
        "computer as the activity log's owner, or undo the per-computer logs."))
    ap.add_argument("--workspace", required=True)
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--own-live-log", action="store_true",
                   help="make THIS computer the owner of the activity log")
    g.add_argument("--undo", action="store_true",
                   help="turn routing off and fold every computer's rows back")
    g.add_argument("--routing-on", action="store_true",
                   help="turn per-computer routing back on after an undo")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --undo: say what it would do, write nothing")
    a = ap.parse_args(argv)
    if a.own_live_log:
        out = set_owner(a.workspace)
    elif a.undo:
        out = undo(a.workspace, apply=not a.dry_run)
    elif a.routing_on:
        out = set_routing(a.workspace, on=True)
    else:
        out = health(a.workspace)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out.get("ok", True) else 1


__all__ = ["COMPUTERS_DIRNAME", "PRESENT_NAME", "SWITCH_ENV", "COUNT_FIELD",
           "COMPUTER_FIELD", "known_marks", "route", "merged_seq_floor",
           "computer_files_floor",
           "own_count", "stamp", "PIN_NAME", "OFF_NAME", "OWNER_FIELD",
           "SWITCH_RECEIPT", "UNDO_RECEIPT", "routing_off", "pinned_owner",
           "owner_of", "set_owner", "set_routing", "undo", "health"]


if __name__ == "__main__":
    raise SystemExit(main())
