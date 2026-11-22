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
import os
import re
from pathlib import Path
from typing import Iterator, Optional

try:
    from event_time import event_time
    from read_alarm import record_read_alarm
    from data_root import resolve as _resolve_data_root
except ImportError:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_time import event_time
    from read_alarm import record_read_alarm
    from data_root import resolve as _resolve_data_root

_SHARD_RE = re.compile(r"^events-(\d{4})\.jsonl$")


#: cr1#1376: the folder that makes a directory a WORKSPACE ROOT rather than a
#: data folder handed in directly. A data folder (`_hq/data`, or a fixture's
#: own folder of log files) never holds an `_hq/` of its own.
WORKSPACE_HQ_DIRNAME = "_hq"


def _holds_log(data_dir: Path) -> bool:
    """True when `data_dir` holds the activity log: the live file or a year
    shard. Read-only; an unreadable folder holds nothing."""
    try:
        if (data_dir / "events.jsonl").is_file():
            return True
        return any(_SHARD_RE.match(p.name) for p in data_dir.glob("events-*.jsonl"))
    except OSError:
        return False


def is_workspace_root(root: str | Path) -> bool:
    """cr1#1376: True when `root` is a workspace root (it holds `_hq/`), not a
    data folder passed directly."""
    try:
        return (Path(root) / WORKSPACE_HQ_DIRNAME).is_dir()
    except OSError:
        return False


def stray_root_log(root: str | Path) -> Optional[Path]:
    """cr1#1376: the stray `events.jsonl` at a workspace root's top, or None.

    Only a WORKSPACE ROOT (`is_workspace_root`) can hold a stray: a data
    folder passed directly keeps its own log at its top by design."""
    root = Path(root)
    if root.name == "events.jsonl" or _SHARD_RE.match(root.name):
        return None
    stray = root / "events.jsonl"
    try:
        if is_workspace_root(root) and stray.is_file():
            return stray
    except OSError:
        return None
    return None


def _data_dir(root: str | Path) -> Path:
    """Resolve the `_hq/data` dir. Accepts a workspace root, the data dir, or a path
    to events.jsonl / a shard file itself.

    cr1#1376: a root-level `events.jsonl` never wins over the real log. A
    folder with `events.jsonl` at its top is read as a data folder passed
    directly ONLY when it is not a workspace root (no `_hq/`), or when it is
    one whose data folder holds no log at all: that one case is left as it
    was, pending the owner's ruling, because the stray may then be the only
    log there is."""
    root = Path(root)
    if root.name == "events.jsonl" or _SHARD_RE.match(root.name):
        return root.parent
    if root.name == "data":
        return root
    if (root / "events.jsonl").exists():
        if is_workspace_root(root):
            real = _resolve_data_root(root)
            if _holds_log(real):
                return real
        return root
    # SPEC SYNC1 B1 — route the workspace-root case through the (dormant)
    # resolver. Byte-identical to `root / "_hq" / "data"` with no override.
    return _resolve_data_root(root)


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


# ---- one log per computer: the merged reader (cr1#1061, parent #790) ----
#
# A seat used from two computers through a sync service must never have two
# writers on one file: the service keeps one version and parks the other as a
# conflict copy (#724), or flushes a stale copy over a newer one (#385). The
# fix gives each computer its own log, in a folder BESIDE the live file (owner
# ruling A2: the live data files stay where they are):
#
#     _hq/data/events.jsonl                      today's file, never moved
#     _hq/data/computers/<writer>/events.jsonl   one per computer
#
# and every reader sees ONE merged view: everything from every file, exact
# duplicates dropped. THIS block is that reader. It writes nothing. A seat
# with no `computers/` folder -- every seat today -- takes the first branch of
# `iter_events` and reads byte for byte as before; the merge only runs when a
# second stream exists.

#: The folder beside the live log that holds one sub-folder per computer.
COMPUTER_LOGS_DIRNAME = "computers"

#: The field a per-computer entry carries its content fingerprint in. The
#: reader never trusts a stored value: it recomputes over the entry without it.
FINGERPRINT_FIELD = "fp"


def _chrono_files(data_dir: Path, floor: Optional[int] = None) -> list[Path]:
    """One log folder's files, chronological: year shards then the active
    file. The same rule `shard_paths` applies to `_hq/data` itself."""
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


def computer_log_dirs(root: str | Path) -> list[Path]:
    """Every per-computer log folder (`_hq/data/computers/<writer>/`) holding
    at least one log file, sorted by writer name. [] on every seat today."""
    base = _data_dir(root) / COMPUTER_LOGS_DIRNAME
    try:
        if not base.is_dir():
            return []
        dirs = sorted((d for d in base.iterdir() if d.is_dir()),
                      key=lambda d: d.name)
    except OSError:
        return []
    return [d for d in dirs if _chrono_files(d)]


def has_computer_logs(root: str | Path) -> bool:
    """True when at least one per-computer log exists beside the live file."""
    return bool(computer_log_dirs(root))


def log_streams(root: str | Path, since_ts=None) -> list[tuple[str, list[Path]]]:
    """`[(name, files), ...]`: the live log first (its shards then
    `events.jsonl`, named ""), then each computer's folder by writer name.
    One computer, today: exactly `[("", shard_paths(root))]`."""
    out: list[tuple[str, list[Path]]] = [("", shard_paths(root, since_ts=since_ts))]
    floor = _floor_year(since_ts)
    for d in computer_log_dirs(root):
        out.append((d.name, _chrono_files(d, floor)))
    return out


def log_files(root: str | Path, since_ts=None) -> list[Path]:
    """Every file the merged reader reads, in stream order. For a stat
    signature or a readability probe -- a cache keyed on `shard_paths` alone
    would not notice the other computer's appends."""
    return [p for _name, files in log_streams(root, since_ts=since_ts)
            for p in files]


def fingerprint(ev: dict) -> str:
    """The content fingerprint of one entry: sha256 over its canonical JSON,
    without the fingerprint field itself. Two entries are exact duplicates
    when, and only when, their fingerprints match."""
    import hashlib
    body = {k: v for k, v in ev.items() if k != FINGERPRINT_FIELD}
    canon = json.dumps(body, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:32]


def _int_seq(ev: dict) -> Optional[int]:
    s = ev.get("seq")
    if isinstance(s, int) and not isinstance(s, bool):
        return s
    return None


def merge_streams(streams: list[list[dict]], report: dict | None = None) -> list[dict]:
    """Merge already-parsed streams into one list: everything from every
    stream, exact duplicates (same `fingerprint`) dropped.

    One stream is returned AS IS (same list, nothing fingerprinted), which is
    what keeps a one-computer seat byte-identical.

    Order: each stream keeps its own order (only heads are compared), and the
    heads interleave by number, `seq`; an entry without a usable number keeps
    its place behind the last numbered entry of its own stream. Ties go to the
    earlier stream (the live log first, then computers by writer name).

    Exact duplicates are counted as a multiset: an entry is kept as many
    times as the ONE stream holding it most often holds it. So an entry in
    two files shows once, and a file's own repeated line is kept, as every
    reader keeps it today, whichever stream comes first.

    `report`, when a dict, is filled with `streams`, `kept`,
    `duplicates_dropped` and `seq_collisions` (a number held by entries with
    different content in two streams -- possible when two computers number an
    entry while out of sync; listed, never resolved here)."""
    if len(streams) <= 1:
        out = streams[0] if streams else []
        if isinstance(report, dict):
            report.update({"streams": len(streams), "kept": len(out),
                           "duplicates_dropped": 0, "seq_collisions": []})
        return out
    import heapq

    def _keyed(rank: int, evs: list[dict]):
        carried = -1
        for idx, ev in enumerate(evs):
            s = _int_seq(ev)
            if s is not None:
                carried = s
            yield (carried, rank, idx), rank, ev

    fps: list[list[str]] = [[fingerprint(ev) for ev in s] for s in streams]
    allowed: dict[str, int] = {}
    for stream_fps in fps:
        counts: dict[str, int] = {}
        for fp in stream_fps:
            counts[fp] = counts.get(fp, 0) + 1
        for fp, n in counts.items():
            if n > allowed.get(fp, 0):
                allowed[fp] = n
    emitted: dict[str, int] = {}
    by_seq: dict[int, dict[str, int]] = {}
    out: list[dict] = []
    dropped = 0
    for (_c, rank, idx), _rank, ev in heapq.merge(
            *(_keyed(r, s) for r, s in enumerate(streams)), key=lambda t: t[0]):
        fp = fps[rank][idx]
        if emitted.get(fp, 0) >= allowed[fp]:
            dropped += 1
            continue
        emitted[fp] = emitted.get(fp, 0) + 1
        s = _int_seq(ev)
        if s is not None:
            by_seq.setdefault(s, {}).setdefault(fp, rank)
        out.append(ev)
    if isinstance(report, dict):
        collisions = sorted(s for s, fps in by_seq.items()
                            if len(set(fps.values())) > 1)
        report.update({"streams": len(streams), "kept": len(out),
                       "duplicates_dropped": dropped,
                       "seq_collisions": collisions})
    return out


def merged_lines(path: str | Path, encoding: str = "utf-8",
                 errors: Optional[str] = None) -> list[str]:
    """The lines of the live `events.jsonl`, merged with every computer's own
    log (cr1#1061 slice 2), for a reader that parses lines itself.

    With no per-computer log beside `path` (every seat with one computer),
    or for any other path, this is exactly
    `Path(path).read_text(encoding, errors).splitlines()` -- same lines, same
    exceptions. Otherwise each file's lines keep their order, the files
    interleave by number like `merge_streams`, and an exact duplicate line
    from another computer's file is dropped (same multiset rule). A line that
    does not parse is kept in its place."""
    import heapq
    p = Path(path)
    kw = {"encoding": encoding}
    if errors is not None:
        kw["errors"] = errors
    if p.name != "events.jsonl" or p.parent.parent.name == COMPUTER_LOGS_DIRNAME:
        return p.read_text(**kw).splitlines()
    dirs = computer_log_dirs(p.parent)
    if not dirs:
        return p.read_text(**kw).splitlines()
    streams: list[list[str]] = [p.read_text(**kw).splitlines() if p.exists() else []]
    for d in dirs:
        lines: list[str] = []
        for f in _chrono_files(d):
            lines.extend(f.read_text(**kw).splitlines())
        streams.append(lines)
    parsed: list[list[tuple[Optional[str], Optional[int]]]] = []
    for lines in streams:
        row = []
        for ln in lines:
            try:
                ev = json.loads(ln) if ln.strip() else None
            except ValueError:
                ev = None
            if isinstance(ev, dict):
                row.append((fingerprint(ev), _int_seq(ev)))
            else:
                row.append((None, None))
        parsed.append(row)
    allowed: dict[str, int] = {}
    for row in parsed:
        counts: dict[str, int] = {}
        for fp, _s in row:
            if fp:
                counts[fp] = counts.get(fp, 0) + 1
        for fp, n in counts.items():
            allowed[fp] = max(allowed.get(fp, 0), n)

    def _keyed(rank: int):
        carried = -1
        for idx, (fp, s) in enumerate(parsed[rank]):
            if s is not None:
                carried = s
            yield (carried, rank, idx)

    emitted: dict[str, int] = {}
    out: list[str] = []
    for _c, rank, idx in heapq.merge(*(_keyed(r) for r in range(len(streams)))):
        fp = parsed[rank][idx][0]
        if fp is not None:
            if emitted.get(fp, 0) >= allowed[fp]:
                continue
            emitted[fp] = emitted.get(fp, 0) + 1
        out.append(streams[rank][idx])
    return out


def open_merged(path: str | Path, encoding: str = "utf-8",
                errors: Optional[str] = None):
    """`open(path, "r", ...)` for a reader that iterates a file's lines, with
    every computer's log merged in (cr1#1061). With no per-computer log it IS
    `open(path, "r", encoding=..., errors=...)`, the very same file object."""
    import io
    p = Path(path)
    if (p.name != "events.jsonl" or p.parent.parent.name == COMPUTER_LOGS_DIRNAME
            or not computer_log_dirs(p.parent)):
        kw = {"encoding": encoding}
        if errors is not None:
            kw["errors"] = errors
        return open(p, "r", **kw)
    return io.StringIO("".join(ln + "\n"
                               for ln in merged_lines(p, encoding, errors)))


def _iter_stream(files: list[Path]) -> Iterator[dict]:
    for p in files:
        yield from _iter_file(p)


def merge_report(root: str | Path) -> dict:
    """What the merged view is made of, for system health and tests:
    `{writers, streams, per_stream, kept, duplicates_dropped, seq_collisions}`.
    Read-only."""
    streams = log_streams(root)
    parsed = [list(_iter_stream(files)) for _name, files in streams]
    rep: dict = {}
    merge_streams(parsed, report=rep)
    rep["writers"] = [name for name, _files in streams if name]
    rep["per_stream"] = {(name or "events.jsonl"): len(evs)
                         for (name, _f), evs in zip(streams, parsed)}
    return rep


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


def _record_stale_read(root: str | Path, reader: str) -> None:
    """SPEC SYNC1 A1 — read-side staleness evidence. Once per load, compare the
    ACTIVE file's visible max seq against the recorded high-water; on a
    regression drop a `.stalereads.json` sidecar (the read_alarm pattern) so a
    between-fires stale-read window leaves evidence even though the reader still
    proceeds (D-2 warn tier: a degraded view beats no view for read-only
    surfaces). Best-effort — NEVER raises, never blocks a read. Freshness applies
    to the active file only (shards are frozen years; seqhw tracks the active
    file).

    NOT UNDER THE HELPER DOOR (MF-M2-13, 2026-09-20). `workspace_access
    run_helper` promises in its own docstring that "this verb writes nothing at
    all" and its allow-list is read/compute only — but `count_rows` and
    `load_all` reach here two frames down, and this best-effort sidecar made
    the read door a writer on a merged seat, where the write would land on the
    customer's own machine with none of the fences the two write verbs carry.
    The layer stamps `CR_HELPER_DOOR` on every helper child process, and that
    is the one condition under which the record is skipped. Every other
    reader — a legacy seat's in-shell python, an in-process import, any
    surface calling this module directly — records exactly as before, byte for
    byte; `tests/run_orch1_test.py` pins both halves.

    THE DOOR TEST IS INSIDE THE `try` (MF-M2-25). Round one put it above,
    which quietly dropped the "NEVER raises" promise two lines up: a bad
    `root` returned at base and raised `TypeError` under the door, and a
    raise here becomes a refused helper call instead of a degraded read —
    the one thing a best-effort diagnostic must never do."""
    try:
        if os.environ.get("CR_HELPER_DOOR"):
            from atomic_write import events_freshness
            from read_alarm import hold_sidecar
            fresh = events_freshness(active_path(root))
            if fresh.get("regressed"):
                hold_sidecar({"kind": "stale_read",
                              "file": active_path(root).name,
                              "file_max_seq": fresh.get("file_max_seq"),
                              "seqhw_max": fresh.get("seqhw_max"),
                              "last_reader": str(reader)[:80],
                              "written": False})
            return
        from atomic_write import events_freshness
        ap = active_path(root)
        fr = events_freshness(ap)
        if not fr.get("regressed"):
            return
        import datetime as _dt
        from atomic_write import atomic_write_json
        sidecar = ap.with_name(ap.name + ".stalereads.json")
        prior_count = 0
        first_seen = None
        try:
            prior = json.loads(sidecar.read_text(encoding="utf-8"))
            if isinstance(prior, dict):
                if isinstance(prior.get("count"), int):
                    prior_count = prior["count"]
                first_seen = prior.get("first_seen")
        except (OSError, json.JSONDecodeError):
            pass
        now = _dt.datetime.now(_dt.timezone.utc).isoformat()
        gap = None
        if isinstance(fr.get("seqhw_max"), int) and isinstance(fr.get("file_max_seq"), int):
            gap = fr["seqhw_max"] - fr["file_max_seq"]
        atomic_write_json(sidecar, {
            "file": ap.name,
            "first_seen": first_seen or now,
            "last_seen": now,
            "count": prior_count + 1,
            "file_max_seq": fr.get("file_max_seq"),
            "seqhw_max": fr.get("seqhw_max"),
            "seq_gap": gap,
            "last_reader": str(reader)[:80],
        })
    except Exception:
        pass


def iter_events(root: str | Path, since_ts=None) -> Iterator[dict]:
    """Generator over every event across all shards + the active file, in chronological
    file order. `since_ts` (ISO string or 'YYYY...') skips whole shards whose year is
    below the floor — a cheap, filename-only prune that never opens a too-old shard.

    ONE LOG PER COMPUTER (cr1#1061): when per-computer logs exist beside the
    live file, this is the merged view (`merge_streams`): every computer's
    entries, exact duplicates dropped. With none -- every seat today -- the
    first branch below is the whole function, unchanged."""
    _record_stale_read(root, reader="events_io.iter_events")
    streams = log_streams(root, since_ts=since_ts)
    if len(streams) == 1:
        for p in streams[0][1]:
            yield from _iter_file(p)
        return
    yield from merge_streams([list(_iter_stream(files))
                              for _name, files in streams])


def load_all(root: str | Path, since_ts=None) -> list[dict]:
    """Materialized `iter_events` — convenience for callers that want a list."""
    return list(iter_events(root, since_ts=since_ts))


def count_rows(root: str | Path, since_ts=None) -> int:
    """How many events this workspace's ledger holds. BOOT3 (2026-09-19).

    The cheapest read that proves a whole path works. A scheduled chat in the
    merged environment has to reach across two machines before it can do
    anything, and this is the one call its bootloader makes to check that the
    helpers, the workspace and the mount are all reachable together — so it
    counts rather than loads, and it goes through `iter_events` so the shard
    rules and the stale-read record are exactly the ones every other reader
    gets.
    """
    return sum(1 for _ in iter_events(root, since_ts=since_ts))


# ---- owner-tier seam (READER1 fix round 2026-08-28) ----
# PURE ADDITION on a new-files-only charter — scope deviation, flagged in
# docs/history/handoffs/2026-08/BUILD_READER1_2026-08-28.md for reviewer sign-off. The import
# fallback below deliberately DUPLICATES load_events_org_scoped's (rather
# than extracting a shared helper) so no existing line in this file changes.

def load_events_owner_scoped(
    root: str | Path, since_ts=None,
) -> tuple[list[dict], list[dict]]:
    """THE defensive events reader for OWNER-TIER composed surfaces — the
    org reader's layer 1 alone: shard-transparent defensive load with the
    skipped-lines channel preserved, and NO account mask, NO personal-lane
    drop. Owner surfaces legitimately see everything (the PGUARD1 D1
    contract: "the raw read still sees everything — owner surfaces keep
    their view"); what this adds over a raw `iter_events` is the skipped
    channel a composed payload must be able to disclose.

    READER1 firewall re-route (2026-08-28): `load_thread_knowledge`'s
    owner-privacy profiles (go / catch-all / call-prep) read through here so
    the raw read lives inside THIS module — the allowlisted shard reader —
    instead of adding a new raw-read site to the structural guard's list.

    Org/board/client/external outputs must NEVER call this — they read via
    `load_events_org_scoped` / `iter_events_org_scoped`, where the mask and
    the personal-lane drop are the default. Same contract, same direction,
    as the RAW_READ_ALLOW header in run_personal_firewall_test.

    Returns (events, skipped) — same shape as `load_events_org_scoped`.
    """
    try:
        from cru_match import load_events_defensively
    except ImportError:  # pragma: no cover — direct-path fallback
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import load_events_defensively

    _record_stale_read(root, reader="events_io.load_events_owner_scoped")
    return load_events_defensively(active_path(root), since_ts=since_ts)


# ---- org-scoped reader (SPEC PGUARD1 D1) ----

def load_events_org_scoped(
    root: str | Path, since_ts=None, drop_personal: bool = True,
    stats: dict | None = None,
) -> tuple[list[dict], list[dict]]:
    """THE events reader for org/board/client/external outputs — the mask is
    the default, not a call-site opt-in.

    Every org-facing rollup (operator-report, weekly-recap,
    board-pack-assembler, boardroom, advisor-export, value_receipt, the
    staff-meeting proposal load) reads through here instead of a raw load, so
    a new surface cannot silently skip the privacy layers. Three layers, in
    order:

      1. defensive shard-transparent load via cru_match.load_events_defensively
         (skipped-lines channel preserved — callers surface it);
      2. account-scope mask (account_scope_gate.filter_masked_events, R5):
         a business→personal account reclassification hides that account's
         history from org output;
      3. personal-lane drop (personal_leak.is_personal): personal reminder
         rows, personal-tie rows, and BAL1 balance-nudge rows never reach an
         org data view (`drop_personal=False` opts out — for the rare org
         reader that provably renders no row content).

    Returns (events, skipped). `since_ts` passes through to the loader's
    shard pruning; masks are computed from the loaded list with a
    workspace-root fallback so a pruned shard can't hide a mask event.

    WITHHELD COUNTER (BUG-8330 fix round, FX-5). Pass a dict as `stats` and it
    is filled with:

      personal_withheld          rows layer 3 removed, total
      personal_withheld_by_tie   the subset removed by the PERSON-TIE JOIN
                                 specifically (item 12's inferred rule, the
                                 one with no explicit marker behind it)

    A ZERO IS WRITTEN WHEN THE LAYER RAN AND REMOVED NOTHING. A KEY IS ABSENT
    WHEN THE LAYER DID NOT RUN — `drop_personal=False`, or the firewall symbol
    failing to import (fix round 2, finding C-6). Those two states are not the
    same fact, and a zero for the second one is precisely the "measured,
    nothing withheld" ambiguity this counter exists to end. Read an absent key
    as "not measured", never as zero.

    The drop used to be a bare list comprehension with nothing recording its
    size, so a 70→6 collapse would have been invisible in exactly the lane
    item 6 was given `n_filtered_by_confidence` to make visible.

    WHO ACTUALLY CARRIES IT (census, kept honest — fix round 2, finding C-4).
    Passing `stats=` is per-call-site, so the counter reaches a surface only
    where a real receipt or run report can hold it. In-tree today:

      value_receipt.compute_value_receipt      -> the `value_receipt_generated`
                                                  org receipt payload
      org_value_detector.detect_org_value_signals
                                               -> its run-report counts dict
      load_thread_knowledge (org profiles)     -> `coverage.personal_withheld`
                                                  in the composed payload
                                                  (READER1 fix round
                                                  2026-08-28: the payload's
                                                  coverage block is a real
                                                  disclosure surface — the
                                                  absent-vs-zero rule rides
                                                  through it end to end)

    The remaining org-scoped reads — `brain_proposals._load_events`,
    `coach_state`'s member-pack seam, `entity_signal_detector._load_events`,
    `objective_link_detector._load_events`, `value_receipt.validate_receipt_ran`
    — drop rows UNCOUNTED, because none of them writes a receipt and inventing
    a receipt type to carry a counter is worse than saying so here.
    `run_personal_tie_join_test` pins this census (all three carriers)
    against the tree, so the paragraph cannot drift from the code.

    **M-REVIEWABLE SEAM:** what belongs on an org surface when a person is
    both client and family is a policy call, not a code call. The shipped
    policy is "a GENUINE business binding wins, and what is withheld is
    measured" — `personal_leak.business_binding`, resolved against the
    workspace's own thread register. The counter is what makes the policy's
    cost visible so M can re-set it with evidence.

    OWNER surfaces (the brief, show-my-reminders, person/org history) should
    NOT use this — they legitimately show personal rows; use `iter_events` +
    their own surface gate. The run_personal_firewall_test structural guard
    enforces which files may read raw.
    """
    try:
        from cru_match import load_events_defensively
    except ImportError:  # pragma: no cover — direct-path fallback
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        from cru_match import load_events_defensively

    _record_stale_read(root, reader="events_io.load_events_org_scoped")
    events, skipped = load_events_defensively(active_path(root), since_ts=since_ts)

    # Layer 2 — account mask. Defensive: a broken mask never blanks a surface
    # (filter_masked_events' own contract); a broken IMPORT leaves events
    # unfiltered the same way the three inline-masker sites tolerate it.
    masks = frozenset()
    try:
        from account_scope_gate import (filter_masked_events,
                                        live_masks, live_masks_from_events)
        if since_ts:
            # Shard pruning can hide mask events in older shards, and a
            # second in-window mask would leave the window computation
            # non-empty — so an "only when empty" fallback still leaks the
            # pruned account's rows. A pruned load ALWAYS takes the
            # full-history mask set (restores are order-dependent too).
            masks = live_masks(root)
        else:
            masks = live_masks_from_events(events)
        events = filter_masked_events(events, masks=masks)
    except Exception:
        pass

    # Layer 3 — personal lane. is_personal never raises. The person-tie JOIN
    # (BUG-8330 item 12): a row referencing a `tie: "personal"` person is
    # personal even when the row itself carries no marker — no capture path
    # ever stamped one, so a family commitment captured from ordinary mail
    # was invisible to this layer before the join.
    n_withheld = 0
    n_withheld_by_tie = 0
    # Did layer 3 actually RUN? Only then may a zero be written — see the
    # docstring's absent-vs-zero rule (fix round 2, finding C-6).
    measured = False
    measured_by_tie = False
    if drop_personal:
        try:
            from personal_leak import is_personal, personal_tie_ids
            # Imported SEPARATELY on purpose: the counter is an accounting
            # nicety, the drop is the privacy wall. A single combined import
            # would let a missing counter symbol take the whole firewall down
            # with it.
            try:
                from personal_leak import withheld_by_personal_tie
            except ImportError:  # pragma: no cover
                withheld_by_personal_tie = None
            # The thread register the join's override resolves against
            # (BUG-8330 fix round 2). Also imported separately, but in the
            # OPPOSITE direction from the counter: a missing resolver must
            # leave the override INERT (empty register -> nothing resolves ->
            # every tie-touching row withheld), never permissive.
            try:
                from personal_leak import business_thread_ids
                b_threads = business_thread_ids(root)
            except ImportError:  # pragma: no cover
                b_threads = frozenset()
            p_ids = personal_tie_ids(root)
            kept = []
            for ev in events:
                if is_personal(ev, masks=masks, personal_ids=p_ids,
                               business_threads=b_threads):
                    n_withheld += 1
                    if (withheld_by_personal_tie is not None
                            and withheld_by_personal_tie(
                                ev, personal_ids=p_ids,
                                business_threads=b_threads)):
                        n_withheld_by_tie += 1
                    continue
                kept.append(ev)
            events = kept
            measured = True
            measured_by_tie = withheld_by_personal_tie is not None
        except ImportError:
            # The firewall symbol itself is missing: the drop DID NOT RUN.
            # Report nothing — a zero here would claim a measurement that
            # never happened, and the caller must be able to tell the two
            # apart. The rows stay in `events` exactly as before; this branch
            # has always been the (near-impossible, same-package) degraded
            # path, and making it silent-and-honest is the whole fix.
            pass

    if isinstance(stats, dict):
        # Zeros are written when MEASURED; keys stay ABSENT when the layer
        # did not run (drop_personal=False, or the import above failed).
        if measured:
            stats["personal_withheld"] = n_withheld
            if measured_by_tie:
                stats["personal_withheld_by_tie"] = n_withheld_by_tie

    return events, skipped


def iter_events_org_scoped(
    root: str | Path, since_ts=None, drop_personal: bool = True,
    stats: dict | None = None,
) -> Iterator[dict]:
    """Generator form of `load_events_org_scoped` (same three layers; the
    skipped-lines channel is dropped — use the load_ form when a surface must
    report skipped counts). `stats` is filled the same way, before the first
    row is yielded."""
    events, _skipped = load_events_org_scoped(
        root, since_ts=since_ts, drop_personal=drop_personal, stats=stats
    )
    yield from events


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


__all__ = ["shard_paths", "active_path", "iter_events", "load_all",
           "count_rows",
           "COMPUTER_LOGS_DIRNAME", "FINGERPRINT_FIELD",
           "computer_log_dirs", "has_computer_logs", "log_streams",
           "log_files", "fingerprint", "merge_streams", "merge_report",
           "merged_lines", "open_merged",
           "load_events_owner_scoped",
           "load_events_org_scoped", "iter_events_org_scoped",
           "shard_invariants",
           "WORKSPACE_HQ_DIRNAME", "is_workspace_root", "stray_root_log"]


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    print("shards:", [p.name for p in shard_paths(root)])
    v = shard_invariants(root)
    print("invariants:", "clean" if not v else v)


# ---- one read of the log for many readers in one process (cr1#1530) ----

class OneLogRead:
    """While open, the defensive log read answers from one in-process copy
    instead of parsing the whole log again for every caller. The copy is keyed
    on the size and change time of every file the read covers, so an append
    during the run is seen. Each caller gets its own list; the entries are
    shared and no reader changes them (`run_hub_refresh_all_test` proves the
    answers equal the uncached ones)."""

    def __enter__(self):
        import sys

        import atomic_write
        import cru_match

        events_io = sys.modules[__name__]
        self._mod = cru_match
        self._events_io = events_io
        self._atomic = atomic_write
        self._real = real = cru_match.load_events_defensively
        self._real_iter = real_iter = events_io.iter_events
        self._real_max = real_max = atomic_write._file_max_seq
        memo: dict = {}
        self.memo = memo

        def cached(events_jsonl_path, since_ts=None):
            try:
                path = Path(events_jsonl_path)
                if path.name == "events.jsonl":
                    files = log_files(path, since_ts=since_ts)
                else:
                    files = [path]
                sig = []
                for f in files:
                    if f.exists():
                        st = f.stat()
                        sig.append((str(f), st.st_size, st.st_mtime_ns))
                key = (str(path), repr(since_ts), tuple(sig))
            except Exception:  # noqa: BLE001 - an unreadable signature is a plain read
                return real(events_jsonl_path, since_ts=since_ts)
            if key not in memo:
                memo[key] = real(events_jsonl_path, since_ts=since_ts)
            events, skipped = memo[key]
            return list(events), list(skipped)

        # The two other whole-log readers a refresh repeats: the plain
        # iterator (about 25 passes) and the stale-read check's scan for the
        # highest number in the file (about 40). Same key, same rule.
        def _sig(files) -> tuple:
            out = []
            for f in files:
                f = Path(f)
                if f.exists():
                    st = f.stat()
                    out.append((str(f), st.st_size, st.st_mtime_ns))
            return tuple(out)

        def cached_iter(root, since_ts=None):
            try:
                key = ("iter", str(root), repr(since_ts),
                       _sig(events_io.log_files(root, since_ts=since_ts)))
            except Exception:  # noqa: BLE001
                return real_iter(root, since_ts=since_ts)
            if key not in memo:
                memo[key] = (list(real_iter(root, since_ts=since_ts)), [])
            return iter(list(memo[key][0]))

        def cached_max(events_path, existing_text=None):
            if existing_text is not None:
                return real_max(events_path, existing_text)
            try:
                key = ("max", str(events_path), _sig([events_path]))
            except Exception:  # noqa: BLE001
                return real_max(events_path)
            if key not in memo:
                memo[key] = real_max(events_path)
            return memo[key]

        # Every name is in hand before the first one is replaced, so a
        # failure above leaves all three as they were.
        cru_match.load_events_defensively = cached
        events_io.iter_events = cached_iter
        atomic_write._file_max_seq = cached_max
        return self

    def __exit__(self, *exc):
        self._mod.load_events_defensively = self._real
        self._events_io.iter_events = self._real_iter
        self._atomic._file_max_seq = self._real_max
        return False
