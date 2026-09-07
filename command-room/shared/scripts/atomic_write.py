#!/usr/bin/env python3
"""
Atomic write helper for Command Room data substrate files (entities.json,
events.jsonl, aliases.json) on Drive-synced workspaces.

THE BUG THIS FIXES (added v2.10.5):

When a skill writes entities.json with `path.write_text(...)` or `open(...,
"w") + .write()` on Windows, the OS does:
  1. truncate the file to zero
  2. write the new content
  3. close

If Drive for Desktop syncs during step 1 or step 2, the partial-write state
gets propagated. Other consumers (Cowork, the same workspace on another
machine, another skill running concurrently) see a truncated or partial
entities.json and either error out or fall back to backups.

Evidence this has happened:
  * `_hq/data/_backups/entities.json.pre-rewrite-20260427-223852.backup` —
    workspace-manager backed up a corrupted state on Apr 27 before
    bash-rewriting a clean version
  * Cracks-watch fire on Apr 28 noted "the live entities.json is currently
    truncated mid-file at person_058" and fell back to a backup
  * Cowork bridge fire on Apr 29 reported the same truncation symptom while
    the live file on disk was actually valid (38353 bytes) — Cowork was
    seeing a stale Drive-sync view of a partial-write state from a prior
    skill's non-atomic write

THE FIX:

Atomic write pattern. Write to a temp sibling, fsync, then rename.

  1. Write content to entities.json.tmp.<unique-id>
  2. fsync the temp file (force OS to flush to disk)
  3. os.replace temp → entities.json (atomic on every modern filesystem)

Drive only ever sees the rename — never the partial-write states. Any
concurrent reader either sees the OLD valid content (before rename) or the
NEW valid content (after rename), never a torn write.

This module is a hard dependency for any skill that writes entities.json,
events.jsonl, aliases.json, or any other workspace data substrate file
that Cowork / cross-machine sync / weekly-audit reads.

USAGE:

    from atomic_write import atomic_write_text, atomic_write_json
    atomic_write_text(path, content_string)
    atomic_write_json(path, dict_or_list, indent=2)

For event-stream-style files use atomic_append_jsonl. Since SPEC INDEX1
Phase A (2026-09-02) it has TWO paths: `events.jsonl` is TRUE-APPENDED
(`O_APPEND|O_BINARY`, one write, fsync, read-back, under the writer lock —
the ledger is never rewritten and the cost of an append no longer grows with
the history); every other JSONL file keeps the whole-file read + atomic
rename, because its contract is whole-file. See `atomic_append_jsonl`.

    from atomic_write import atomic_append_jsonl
    atomic_append_jsonl(path, [event_dict_1, event_dict_2])
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _license_gate_check(path) -> None:
    """LICGATE1 SPIKE — delete with `license_gate.py` before any release.

    Placed in `atomic_write_text` because it is the deepest seam: the JSONL
    append path and `atomic_write_json` both funnel through here, so ONE call
    covers every substrate write without touching either caller.

    Imported with the same lazy, twice-defensive shape as `_clock1()` above —
    this module sits at the bottom of the import graph and the writer must keep
    working if the probe is absent. A missing gate means "cannot evaluate",
    which is a write that proceeds.
    """
    try:
        import license_gate
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import license_gate
        except Exception:
            return
    except Exception:
        return
    license_gate.check(path)


def atomic_write_text(path: str | Path, content: str, encoding: str = "utf-8",
                      create_parents: bool = True) -> None:
    """Write text to `path` atomically. Guarantees that any concurrent reader
    of `path` sees either the old content or the new content, never a torn
    write or a truncated/partial state.

    Implementation: write to a temp sibling, fsync, then os.replace.

    `create_parents` (FOLDERGUARD) decides what happens when the parent
    directory is missing. Default True preserves every `_hq/` substrate caller,
    which must self-create. Pass **False** for any path inside a user's project
    folder: those directories are the CEO's, not ours, and a missing one means
    the *record* is wrong — a mkdir there fabricates a folder that was never
    there and makes the bad record look valid to every later reader. False turns
    that silent corruption into a loud FileNotFoundError.
    """
    path = Path(path)
    _license_gate_check(path)
    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.parent.is_dir():
        raise FileNotFoundError(
            f"refusing to create parent directory for {path} "
            f"(create_parents=False and {path.parent} does not exist)")

    # mkstemp gives us a unique sibling temp file with restrictive perms.
    # We use the same parent directory so os.replace is a same-filesystem rename
    # (which is the atomic guarantee on Windows + POSIX).
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{path.name}.tmp.",
        suffix=".write",
        dir=str(path.parent),
    )
    tmp_path = Path(tmp_path)
    try:
        with os.fdopen(fd, "w", encoding=encoding, newline="") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # On Windows, os.replace overwrites atomically (POSIX-equivalent rename).
        os.replace(tmp_path, path)
    except Exception:
        # If anything went wrong, clean up the temp file. Don't leave it
        # behind (Drive will sync it as garbage otherwise).
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(
    path: str | Path,
    data: Any,
    indent: int | None = 2,
    ensure_ascii: bool = False,
    create_parents: bool = True,
) -> None:
    """Atomic JSON write. Wraps atomic_write_text with json.dumps."""
    content = json.dumps(data, indent=indent, ensure_ascii=ensure_ascii)
    if indent is not None:
        # json.dumps doesn't add a trailing newline; add one for tooling sanity.
        content = content + "\n"
    atomic_write_text(path, content, create_parents=create_parents)


class SubstrateRegressionError(Exception):
    """Raised when an events.jsonl in-place append is refused because the file
    on disk regressed below the recorded seq high-water mark (FS-04) — a stale
    lineage clobbered the live file (the multi-machine Drive last-writer-wins
    hazard). The batch is quarantined, never lost."""


def _seqhw_path(events_path: Path) -> Path:
    return events_path.with_name(events_path.name + ".seqhw")


def _read_seqhw(events_path: Path) -> int | None:
    """The recorded max seq high-water for this events log, or None if no
    sidecar yet. The sidecar lives next to events.jsonl (Drive-synced), so it
    travels with the file and — being small — syncs down BEFORE the big log,
    which is exactly what makes a stale-log/fresh-sidecar clobber detectable."""
    p = _seqhw_path(events_path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        v = raw.get("max_seq")
        return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None
    except (OSError, json.JSONDecodeError, ValueError, AttributeError):
        return None


def _write_seqhw(events_path: Path, max_seq: int) -> bool:
    """Advance the `.seqhw` sidecar. Returns True on success, False when the
    write failed. Never raises: for the repair / reconcile callers the sidecar
    is a guard, not a hard dependency. The APPEND path (INDEX1 Phase A, R2)
    reads the False and refuses to stay silent — see `_note_seqhw_write_failed`."""
    import datetime as _dt
    try:
        atomic_write_json(
            _seqhw_path(events_path),
            {"max_seq": int(max_seq),
             "updated": _dt.datetime.now(_dt.timezone.utc).isoformat()},
        )
        return True
    except Exception:
        return False


class SeqHighWaterError(Exception):
    """Raised AFTER a successful events append when the `.seqhw` sidecar could
    not be advanced AND the rescan marker that would force the next append to
    re-derive the maximum from the whole file could not be written either
    (INDEX1 Phase A, R2). The batch IS on disk — the message says so — but the
    seq allocator has lost both of its safety nets, and silence here is how a
    reused seq would enter the ledger."""


class AppendVerificationError(Exception):
    """Raised when the post-append read-back does not find the batch at the
    end of the events file (INDEX1 Phase A, R4). The classic shape is a POSIX
    inode swap: another process replaced the file (rotation / repair outside
    the writer lock) between our open and our write, so the bytes landed on
    the orphaned inode. The batch is set aside in a `.appendverify-*.jsonl`
    side file (NOT the `.quarantine-*` family, so it is never auto-replayed —
    a false negative must not become a duplicate) and `.seqhw` is NOT
    advanced."""


def _rescan_marker_path(events_path: Path) -> Path:
    """`events.jsonl.seqhw.rescan.json` — present when a previous append could
    not advance `.seqhw`. Forces the next append to derive the maximum from a
    full scan (R2) instead of the bounded tail, then clears itself."""
    return events_path.with_name(events_path.name + ".seqhw.rescan.json")


def _note_seqhw_write_failed(events_path: Path, intended_max_seq: int,
                             holder: str) -> None:
    """R2 — a failed `.seqhw` advance after a successful append must not
    silently allow a reused seq. Write the rescan marker (best-effort, two
    ways); if even that fails, raise SeqHighWaterError."""
    import datetime as _dt
    marker = _rescan_marker_path(events_path)
    body = {
        "reason": "seqhw_write_failed",
        "detected": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "intended_max_seq": int(intended_max_seq),
        "holder": holder,
    }
    try:
        atomic_write_json(marker, body)
        return
    except Exception:
        pass
    try:
        with open(marker, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(body) + "\n")
        return
    except Exception:
        pass
    raise SeqHighWaterError(
        f"the batch (max seq {int(intended_max_seq)}) IS on disk, but the "
        f".seqhw high-water sidecar could not be advanced and the rescan "
        f"marker {marker.name} could not be written either (holder={holder}). "
        f"Until one of them can be written, the seq allocator has no witness "
        f"against a clobbered file. Check disk space / permissions on "
        f"{marker.parent}."
    )


def _clear_rescan_marker(events_path: Path) -> None:
    try:
        _rescan_marker_path(events_path).unlink()
    except OSError:
        pass


# INDEX1 Phase A — the append path opens the ledger in BINARY mode on every
# platform. On Windows a text-mode CRT fd rewrites "\n" as "\r\n" on write,
# and a CRLF ledger is a different file from the one every reader parses;
# `O_BINARY` exists only on Windows, so the flag is 0 elsewhere.
_O_BINARY = getattr(os, "O_BINARY", 0)

# How much of the ledger's tail the appender reads to learn the two facts it
# needs — the max human-counter seq and the newest timestamp. 64 KiB is ~100
# rows of the operator's ledger (≈700 B/row) and ~10x the largest batch any
# writer produces; the read is O(1) in file size, which is the whole point
# of Phase A. A tail that disagrees with `.seqhw` falls back to a full scan
# (see `_read_stamp_write`), so a smaller-than-ideal window costs time, never
# correctness.
_TAIL_READ_BYTES = 64 * 1024


class _TailView:
    """What the bounded tail read learned. `text` is the decoded run of
    COMPLETE lines inside the window (a partial first line is dropped when
    the window starts mid-file); `raw_all` is the untrimmed window bytes;
    `whole_file` says the window covered the file from byte 0, in which case
    `text` is the entire ledger and the tail max IS the file max."""

    __slots__ = ("text", "raw_all", "size", "ends_with_newline", "whole_file")

    def __init__(self, text: str, raw_all: bytes, size: int,
                 ends_with_newline: bool, whole_file: bool) -> None:
        self.text = text
        self.raw_all = raw_all
        self.size = size
        self.ends_with_newline = ends_with_newline
        self.whole_file = whole_file


def _read_events_tail(path: Path, encoding: str = "utf-8",
                      max_bytes: int | None = None) -> _TailView:
    """Bounded tail read of a JSONL ledger (INDEX1 Phase A, R1).

    Opens read-only in binary, seeks to `size - max_bytes`, reads to EOF, and
    drops the (possibly partial) first line when the window started mid-file
    so every line in `text` is a complete, newline-delimited record. An
    absent file reads as empty. Raises OSError for anything else — the
    appender must not allocate against a ledger it could not read.
    """
    if max_bytes is None:
        max_bytes = _TAIL_READ_BYTES
    try:
        fd = os.open(str(path), os.O_RDONLY | _O_BINARY)
    except FileNotFoundError:
        return _TailView("", b"", 0, True, True)
    try:
        size = os.fstat(fd).st_size
        start = max(0, size - int(max_bytes))
        if start:
            os.lseek(fd, start, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = size - start
        while remaining > 0:
            b = os.read(fd, min(remaining, 1 << 20))
            if not b:
                break
            chunks.append(b)
            remaining -= len(b)
        raw_all = b"".join(chunks)
    finally:
        os.close(fd)
    whole_file = start == 0
    raw = raw_all
    if not whole_file:
        nl = raw.find(b"\n")
        raw = raw[nl + 1:] if nl >= 0 else b""
    ends_with_newline = size == 0 or raw_all.endswith(b"\n")
    return _TailView(raw.decode(encoding, errors="replace"), raw_all, size,
                     ends_with_newline, whole_file)


def _append_bytes(path: Path, payload: bytes) -> None:
    """True append (INDEX1 Phase A, R5): `O_APPEND|O_WRONLY|O_CREAT` in binary
    mode, the payload written as ONE `os.write` (the loop only runs again on
    a short write, which regular files do not produce short of disk-full),
    then `os.fsync`. No temp file, no rename — the ledger is never rewritten
    on the append path, so a concurrent reader holding the file open never
    forces a Windows `os.replace` sharing-violation retry, and the cost of an
    append no longer grows with the history."""
    fd = os.open(str(path), os.O_APPEND | os.O_WRONLY | os.O_CREAT | _O_BINARY,
                 0o644)
    try:
        view = memoryview(payload)
        while len(view):
            n = os.write(fd, view)
            view = view[n:]
        os.fsync(fd)
    finally:
        os.close(fd)


# EPOCH_THRESHOLD — any seq at/above this is a nano-epoch artifact (1.77e18…),
# not a human counter. Shared by the tail scan + the auto-stamp step, kept in
# one place so the "ignore ≥1e10" contract can never drift between them.
_EPOCH_THRESHOLD = 10**10

# BUG-8330 item 8 — bound on an explicit caller-supplied seq ABOVE the
# ledger's max. The honor-explicit branch accepted anything < 10^10, so one
# hand-stamped `"seq": 999999` relocated the whole allocation ceiling (both
# allocators are max+1, and the .seqhw sidecar then locks the jump in — a
# real ledger now allocates above 1,000,000). One rotation window is 10k;
# a legitimate explicit seq never leads the ledger by anywhere near that.
# Beyond the gap the seq is REASSIGNED like a stale one, with loud stderr.
# Already-relocated ledgers: shared/scripts/repair_seq_relocation.py
# (supervised one-shot remap).
SEQ_GAP_MAX = 1000

# CLOCKTS1 — the SYMMETRIC bound on an explicit caller-supplied `ts` ahead of
# the machine clock. `SEQ_GAP_MAX` above has bounded a suspicious explicit
# `seq` since BUG-8330; the same file honoured any explicit `ts` verbatim, and
# a single future-dated row became the ledger maximum, was adopted as a
# clock-skew floor, and re-stamped itself onto every later append.
#
# WHY A DAY AND NOT THE 300s TOLERANCE. This bound REFUSES a write, so it may
# only catch rows that are certainly wrong. A rounded placeholder written for a
# same-day item legitimately lands a few hours ahead (the field case: a
# weekly-recap interaction stamped at local noon), and refusing those would
# break a real workflow to fix a stamp. So the division of labour is:
#
#   this gate REFUSES the certainly-wrong — a row dated a day or more ahead,
#   which no writer in the product has any reason to produce;
#   `trusted_now.classify_substrate_max` NEUTRALIZES the merely-suspicious —
#   the row still lands, but it can never become a floor for anything else.
#
# Together they close both scales. Neither alone does. CR_TS_LEAD_GUARD=0
# disables this check, for supervised historical replay and for the repair
# tool — the same escape-hatch shape as CR_SEQ_HIGHWATER=0.
TS_LEAD_MAX_SECONDS = 86400


class FutureTimestampRefused(Exception):
    """Raised when an events append is refused because a caller supplied a `ts`
    leading the machine clock by more than TS_LEAD_MAX_SECONDS. The batch is
    written to a REVIEW side file and never silently re-stamped and never
    silently accepted — a wrong date in the permanent ledger drives wrong
    windows and, since the confirmed-tier age-out, wrong closures."""


def _clock1():
    """The CLOCK1 helper module, or None.

    Lazily imported and fully defensive, with the same sys.path retry the
    dedup hook above uses: this module sits at the bottom of the import graph
    and the append gate must keep working even if the clock helper is missing.
    None everywhere below means "cannot corroborate", which leaves the stamp on
    the machine clock exactly as it was before CLOCK1.
    """
    try:
        import trusted_now

        return trusted_now
    except ImportError:
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            import trusted_now

            return trusted_now
        except Exception:
            return None
    except Exception:
        return None


def _scan_events_text(path: Path, existing_text: str):
    """ONE json.loads pass over the ledger text for BOTH facts the writer needs:
    `(newest_ts_or_None, max_human_counter_seq)`.

    Fused deliberately. The seq scan and the CLOCK1 corroboration read want the
    same parse of the same bytes, and running them separately measured +36ms on
    every append against a multi-megabyte ledger — a cost paid on the hot path
    for no information. Falls back to the seq-only scan if the clock helper is
    unavailable, so the writer never depends on it.
    """
    mod = _clock1()
    if mod is not None:
        try:
            newest, max_seq = mod.scan_jsonl_text(
                existing_text, epoch_threshold=_EPOCH_THRESHOLD)
            return newest, max_seq
        except Exception:
            pass
    return None, _file_max_seq(path, existing_text=existing_text)


def _ts_lead_seconds(raw_ts: str, machine_now) -> float | None:
    """How far `raw_ts` leads `machine_now`, in seconds, or None.

    None means "no opinion" and is returned for anything unparseable — the
    defensive-reader posture every other reader here takes. A `ts` this cannot
    parse is a different defect (event_payload_check's job) and must not be
    turned into a refusal by this guard, which would make an unrelated
    malformed row look like a clock incident.
    """
    try:
        from event_time import parse_ts
    except ImportError:
        try:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from event_time import parse_ts
        except Exception:
            return None
    except Exception:
        return None
    try:
        parsed = parse_ts(raw_ts)
        if parsed is None:
            return None
        return (parsed - machine_now).total_seconds()
    except Exception:
        return None


def _clock1_floor_stamp(machine_now, newest_ts, events_path=None) -> dict:
    """CLOCK1 — the `ts` this append should carry, plus its provenance.

    Returns `{"ts", "ts_source", "machine_ts"}`. When the helper is
    unavailable, or the ledger does not prove the clock is behind, this is the
    machine reading with no annotation — byte-identical to pre-CLOCK1 output.
    `events_path` scopes the anomaly suppression to THIS workspace.
    """
    mod = _clock1()
    plain = {"ts": machine_now, "ts_source": None, "machine_ts": None,
             "refused": None}
    if mod is None:
        return plain
    try:
        return mod.floor_stamp(machine_now, newest_ts,
                               events_path=events_path)
    except Exception:
        return plain


def _file_max_seq(events_path: str | Path, existing_text: str | None = None) -> int:
    """The maximum human-counter seq on disk in `events_path` (0 when the file
    is absent/empty/all-nano-epoch). Ignores non-dict / non-numeric / nano-epoch
    (>=1e10) seqs, exactly like next_seq.py. `existing_text` lets a caller that
    already read the file avoid a second read (the writer's hot path)."""
    path = Path(events_path)
    if existing_text is None:
        try:
            existing_text = path.read_text(encoding="utf-8") if path.exists() else ""
        except OSError:
            return 0
    max_seq = 0
    for line in existing_text.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        s = entry.get("seq")
        if (
            isinstance(s, (int, float))
            and not isinstance(s, bool)
            and s < _EPOCH_THRESHOLD
            and s > max_seq
        ):
            max_seq = int(s)
    return max_seq


def events_freshness(events_path: str | Path) -> dict:
    """SPEC SYNC1 A1 — read-side staleness detection. Compare the max seq
    ACTUALLY VISIBLE in `events_path` against the recorded `.seqhw` high-water.

    A regression (file_max_seq < seqhw_max) means the view we can see is behind
    its own high-water — a mid-sync flush, a stale Drive last-writer-wins copy,
    or (the row-17 class) a Cowork sandbox mount serving a copy from a
    pre-recovery lineage. Reads are stale even with zero laptop involvement, and
    because `atomic_append_jsonl` is read-modify-write, a write through a stale
    view rewrites the whole file from stale content — a stale READ becomes a
    clobbering WRITE. This is the read-path extension of the FS-04 write guard.

    Returns {file_max_seq, seqhw_max, regressed, checked_at}. `seqhw_max` is
    None on a workspace with no sidecar yet (a fresh log) — `regressed` is False
    there (nothing to regress below), so single-machine / brand-new workspaces
    no-op clean (back-compat, D-5).

    An ABSENT events.jsonl with a live `.seqhw` sidecar IS regressed (second-eyes
    fix): that view once reached the high-water and now shows nothing — the most
    stale view possible (a partial mount / mid-sync dir). Treating it as fresh
    let the reconciler rebuild the log from a quarantine at seq 1 and silently
    LOWER the high-water — the exact clobber this module exists to prevent. The
    failure direction here must always be refuse."""
    import datetime as _dt
    path = Path(events_path)
    file_max = _file_max_seq(path)
    seqhw = _read_seqhw(path)
    regressed = seqhw is not None and file_max < seqhw
    return {
        "file_max_seq": file_max,
        "seqhw_max": seqhw,
        "regressed": bool(regressed),
        "checked_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
    }


def _regression_marker_path(events_path: Path) -> Path:
    return events_path.with_name(events_path.name + ".seqregression.json")


def _recovery_dir(events_path: Path) -> Path:
    """A fresh, timestamped `_recovery_<stamp>/` under the data dir — where the
    reconciler + the marker truth-check archive resolved artifacts (snapshot-
    never-delete). Created on demand."""
    import datetime as _dt
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    d = events_path.parent / f"_recovery_{stamp}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def check_substrate_regression(events_path: str | Path) -> dict | None:
    """Read the FS-04 regression marker if one was left by a refused append.
    system-health / morning-briefing call this to surface the LOUD alarm.
    Returns the marker dict (with `quarantine_path`, counts, seqs) or None.

    SPEC SYNC1 A2 — TRUTH-CHECK THE MARKER (the row-17 fix). The 2026-07-19
    incident left a marker (file 3591 / sidecar 4606) that outlived its truth:
    the real file on the primary machine was healthy at 4643, but the marker
    lingered and `substrate_alarm_lines` surfaced a data-loss alarm that was
    false by read-time. So before returning a marker, re-verify it: if the live
    file now satisfies `file_max_seq >= sidecar_max_seq`, the condition has
    resolved — archive the marker into a `_recovery_<stamp>/` snapshot (never
    delete) and return None. A still-true marker is returned unchanged."""
    events_path = Path(events_path)
    p = _regression_marker_path(events_path)
    try:
        marker = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(marker, dict):
        return None
    sidecar_max = marker.get("sidecar_max_seq")
    if isinstance(sidecar_max, (int, float)) and not isinstance(sidecar_max, bool):
        # Truth-check: is the condition still true against the live file?
        if _file_max_seq(events_path) >= int(sidecar_max):
            _archive_resolved_marker(events_path, p, marker)
            return None
    return marker


def _archive_resolved_marker(events_path: Path, marker_path: Path, marker: dict) -> None:
    """Best-effort: move a resolved regression marker out of the live path into a
    `_recovery_<stamp>/` snapshot so it can never be re-surfaced as a live alarm,
    while the audit trail is preserved. Never raises — a read-time truth-check
    that could crash the health surface would be a worse bug than the stale
    marker it clears."""
    try:
        dest = _recovery_dir(events_path) / marker_path.name
        atomic_write_json(dest, marker)
        try:
            marker_path.unlink()
        except OSError:
            # If the mount refuses unlink, mv-aside so it stops matching the
            # live marker glob (same doctrine as the lock-litter sweep).
            try:
                import time as _time
                marker_path.rename(
                    marker_path.with_suffix(marker_path.suffix + f".archived.{int(_time.time())}")
                )
            except OSError:
                pass
    except Exception:
        pass


def atomic_append_jsonl(
    path: str | Path,
    events: list[dict[str, Any]] | dict[str, Any],
    encoding: str = "utf-8",
    holder: str = "atomic_append_jsonl",
) -> list[dict[str, Any]]:
    """Append one or more JSON-line records to a JSONL file atomically.

    TWO WRITE PATHS (SPEC INDEX1 Phase A, 2026-09-02):
      * `events.jsonl` — TRUE APPEND. A bounded tail read (last 64 KiB,
        complete lines only) yields the max seq and the newest timestamp;
        seq is allocated from max(file max, `.seqhw`) + 1; the batch lands
        via `O_APPEND|O_WRONLY|O_CREAT|O_BINARY`, one write, fsync; the write
        is read back and must be the file's suffix before `.seqhw` advances.
        A torn tail (no trailing newline) is healed by leading the batch with
        a newline and recorded through the FS-15 read-alarm sidecar — never
        truncated. Append cost is independent of the history's size, and the
        ledger is never rewritten on this path (rotation, reconcile_forward
        and the repair tools keep their whole-file rewrite — out of scope).
      * every other JSONL file — read the existing file, construct the full
        new content, write via atomic_write_text (their contract is
        whole-file).

    RETURNS the written event copies AS STAMPED (BUG-8330 item 7) — for
    events.jsonl that means the allocated `seq` and `ts` are readable from
    the return value. A caller that needs the seq of what it just wrote
    reads it HERE; calling next_seq() first and hand-stamping `"seq"` is the
    racy reserve-then-write pattern that produced duplicate seqs, and
    writer_contract_lint now flags it.

    WRITER LOCK FOR events.jsonl (SPEC A1, v3.19.x):
    For writes to a file named `events.jsonl` specifically, the entire
    read -> auto-stamp -> atomic-rename sequence runs inside the cross-process
    `writer_lock.events_writer_lock` so seq reservation and the write are one
    critical section. This closes the last-writer-wins race documented in
    RELIABILITY.md §3 (two racing callers losing an event or duplicating a
    seq; on Windows the racing os.replace could even raise PermissionError).
    The lock is an OS byte-range lock on `_hq/data/.writer.lock` (kernel
    releases it on crash — zero manual cleanup), with a sentinel fallback on
    unsupported mounts and best-effort contention telemetry. `holder` is an
    optional caller label recorded in the lock diagnostics + any timeout
    message; it defaults so every existing call site keeps working unchanged.
    Non-events.jsonl writes take NO lock (they have their own contracts).

    Use for events.jsonl, staging_emissions.jsonl, classifier_feedback.jsonl,
    .backfill_cursor — anything Cowork or cross-machine sync reads.

    Batched appends (one call with N events) are still preferred where a
    writer has N related events — a batch is one lock hold, one fsync and one
    referentially-linked unit for the FS-04 / CLOCKTS1 set-aside paths.

    DEFENSIVE WRAP (v3.13.8.1 — Bug #68):
    Accepts either a list of dicts (canonical) OR a single dict. A single
    dict gets wrapped to [dict] before the iteration. This closes a corruption
    class observed in v3.13.8 where some commitment writers passed a bare
    dict — `for e in events` iterated the dict's keys, writing each key as
    a malformed line. The helper now tolerates either shape and raises
    TypeError on any other input rather than silently producing garbage.

    AUTO-STAMP seq + ts FOR events.jsonl (v3.13.8.3 — Bug #74 + Bug #75):
    For writes to a file named `events.jsonl` specifically, this helper now
    auto-stamps two canonical fields on every event before the write:
      - `seq` — monotonic human-counter integer, computed inline from the
        existing tail (max human-counter seq + 1, ignoring nano-epoch
        artifacts above 1e10 per next_seq.py contract). Stamped only when
        the caller omitted `seq` or passed None.
      - `ts` — ISO-8601 UTC timestamp. Stamped only when the caller omitted
        `ts`, passed None, or passed an empty/whitespace-only string.
    Bug #74 root cause: 36% of events in M's events.jsonl lacked seq because
    LLM-driven writers (skill prose templates) frequently omitted the field;
    next_seq.py existed but writers didn't always call it. Bug #75 root
    cause: the coach SKILL.md template lacked a `ts` field, so the LLM wrote
    coach_session events without ts. Both fixes are caller-agnostic: even
    a malformed template now produces canonical-shaped events. Caller-side
    seq/ts stamping still works exactly as before — explicit values are
    respected; only missing/empty values are filled.

    For non-events.jsonl writes (staging_emissions.jsonl, classifier
    feedback, etc.), no auto-stamping happens — those files have their own
    schema contracts.
    """
    # LICGATE1 — the true-append fast path (INDEX1 Phase A) writes events.jsonl
    # through _append_bytes and never reaches atomic_write_text, so the gate
    # has to sit here too. Checked BEFORE the writer lock so a refusal never
    # holds it. Fail-open shape is inside _license_gate_check.
    _license_gate_check(path)
    # Defensive wrap — tolerate single-dict callers (Bug #68 fix)
    if isinstance(events, dict):
        events = [events]
    if not isinstance(events, list):
        raise TypeError(
            f"atomic_append_jsonl expects list[dict] or dict, got "
            f"{type(events).__name__}"
        )
    for i, ev in enumerate(events):
        if not isinstance(ev, dict):
            raise TypeError(
                f"atomic_append_jsonl entries must be dicts; entry {i} is "
                f"{type(ev).__name__}: {ev!r}"
            )

    path = Path(path)
    is_events = path.name == "events.jsonl"

    # CLOCK1 — teach the clock helper which workspace this process is working
    # in. The ~20 writer helpers that pre-stamp their own `ts` do it through
    # no-argument `_now_iso()` functions that cannot be handed a root without
    # rewriting every call site, so the seams that DO know the root register it
    # on the way past. Best-effort and silent: an unregistered workspace simply
    # means those helpers keep using the machine clock.
    if is_events:
        _mod = _clock1()
        if _mod is not None:
            try:
                _mod.register_workspace_from_data_path(path)
            except Exception:
                pass

    # EVENT GATE (Phase 1 Foundation F1, 2026-07) — the append_event()
    # gatekeeper runs INSIDE this single append path so every event family is
    # gated from day one, caller-agnostic (same doctrine as the auto-stamp
    # below): type-drift normalization, cmt_<ulid> minting + data.kind on
    # commitments, fail-loud rejection of id-less commitment_resolved, and
    # schema-enum validation. STRICT on both entries as of Phase 4 (2026-07-02)
    # — the F1 burn-in ended with the Phase 1-3 writer migrations: an
    # unregistered event type or kind-less commitment now rejects here too,
    # identical to event_gate.append_event. Gate failures RAISE — they must
    # never be swallowed. CR_EVENT_GATE=0 disables (emergencies only).
    if is_events:
        try:
            from event_gate import gate_events
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from event_gate import gate_events
        events = gate_events(events, strict_enum=True, holder=holder,
                             events_jsonl_path=path)

        # ACCOUNT-SCOPE WALL (connector-agnostic-v1, R2/R3) — the writer-side
        # privacy guarantee, run at this same single chokepoint. A personal /
        # out-of-scope account's mail can never enter events.jsonl; a
        # provenance-REQUIRED family with no provenance is rejected (the R2
        # fail-open fix). NO-OP unless the workspace has classified accounts
        # (R4) — every existing workspace behaves exactly as today. Only raises
        # the deliberate AccountScopeError; an internal error degrades to
        # "allow the write" so a broken account map never bricks the substrate.
        try:
            from account_scope_gate import enforce_scope
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            try:
                from account_scope_gate import enforce_scope
            except Exception:
                enforce_scope = None
        except Exception:
            enforce_scope = None
        if enforce_scope is not None:
            events = enforce_scope(events, path=path, holder=holder)

        # SEMANTIC DEDUP AT CAPTURE (v4.6.0 C4) — the `(source_ref, title)`
        # dedup key is source-scoped, so the same real commitment captured by
        # different writers (meeting + follow-up email + nightly sweep) lands
        # as three open items. This hook compares each new `commitment` in the
        # batch against the OPEN set (owner + counterparty + name-stripped
        # title similarity within a time window) and FLAGS suspects
        # (data.pending_review + data.suspected_duplicate_of) for the confirm
        # flow — never drops, never merges. Runs here, caller-agnostically,
        # for the same reason the gate does. Fail-open: a check failure
        # appends the batch unflagged (today's behavior); it must never lose
        # a capture. CR_DEDUP_CHECK=0 disables.
        try:
            from commitment_dedup import flag_suspected_duplicates
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            try:
                from commitment_dedup import flag_suspected_duplicates
            except Exception:
                flag_suspected_duplicates = None
        except Exception:
            flag_suspected_duplicates = None
        if flag_suspected_duplicates is not None:
            try:
                events = flag_suspected_duplicates(events, path)
            except Exception:
                pass

    # BUG-8330 item 7 — the stamped copies (assigned seq/ts) are RETURNED so
    # callers that need the allocated seq read it from the return value.
    # Pre-computing via next_seq() then stamping "seq" by hand is the racy
    # reserve-then-write pattern this replaces; writer_contract_lint flags it.
    stamped: list[dict[str, Any]] = []

    def _read_stamp_write(evs: list[dict[str, Any]]) -> None:
        existing = ""
        existing_max_seq = 0
        existing_newest_ts = None
        tail: _TailView | None = None
        hw_guard_on = is_events and os.environ.get("CR_SEQ_HIGHWATER", "1") != "0"
        hw: int | None = None
        rescan = False
        if is_events:
            # INDEX1 Phase A (R1/R3) — a BOUNDED tail read replaces the
            # whole-file read. ONE parse pass over the tail gives both answers
            # the writer needs (SPEC SYNC1's seq contract plus CLOCK1's
            # corroboration input): the max human-counter seq and the newest
            # timestamp. Every writer appends rows whose seq is >= everything
            # before them (stale explicit seqs are reassigned below), so on a
            # ledger written by this appender the tail max IS the file max.
            tail = _read_events_tail(path, encoding=encoding)
            existing_newest_ts, existing_max_seq = _scan_events_text(
                path, tail.text)
            if hw_guard_on:
                hw = _read_seqhw(path)
            # WHEN THE TAIL IS NOT ENOUGH — fall back to the full scan the old
            # path always paid. Three cases, all rare on a healthy ledger:
            #   * `.seqhw` is absent or unreadable and the window did not
            #     cover the file: a fresh or legacy ledger may be
            #     non-monotonic mid-file (the operator's has 71 such spots
            #     from the pre-appender era), and with no high-water to
            #     corroborate the tail, only the file itself can say.
            #   * a previous append could not advance `.seqhw` and left the
            #     rescan marker (R2): the sidecar is known to be behind.
            #   * the tail max is BELOW `.seqhw`: either a clobbered stale
            #     lineage (FS-04 below will refuse) or a legitimate ledger
            #     whose maximum sits mid-file. The full scan tells them
            #     apart, so the refusal keeps exactly its old semantics —
            #     "the FILE's max seq regressed below the high-water" — and a
            #     non-monotonic legacy ledger is never refused by mistake.
            #   * the high-water guard is OFF (CR_SEQ_HIGHWATER=0, the
            #     emergency hatch): with no sidecar to corroborate the tail,
            #     the allocator pays the old whole-file scan rather than
            #     trust a window it cannot check.
            rescan = _rescan_marker_path(path).exists()
            needs_full = (not tail.whole_file) and (
                (not hw_guard_on) or hw is None or rescan
                or existing_max_seq < hw)
            if needs_full:
                full_newest, full_max = _scan_events_text(
                    path, path.read_text(encoding=encoding, errors="replace"))
                existing_max_seq = max(existing_max_seq, full_max)
                if full_newest is not None and (
                        existing_newest_ts is None
                        or full_newest > existing_newest_ts):
                    existing_newest_ts = full_newest
        elif path.exists():
            # Non-events files keep the whole-file read + atomic rewrite:
            # their contract is whole-file (SPEC INDEX1 D6).
            existing = path.read_text(encoding=encoding)
            if existing and not existing.endswith("\n"):
                existing = existing + "\n"

        # v3.13.8.3 Bug #74 + #75 — auto-stamp seq + ts for events.jsonl writes.
        # Shallow-copy each event to avoid mutating caller's dicts.
        if is_events:
            import datetime as _dt
            EPOCH_THRESHOLD = _EPOCH_THRESHOLD
            evs = [{**ev} for ev in evs]
            # R1 — the allocation base is max(file max, .seqhw), NEVER the
            # sidecar alone: a sidecar that fell behind the file (a failed
            # advance, a repair that moved the file without it) must not hand
            # out a seq the ledger already holds. With the guard on and no
            # regression, the two agree and this is the file max.
            next_seq_val = max(existing_max_seq, hw if hw is not None else 0) + 1
            # CLOCK1 — a stale machine clock must not stamp the permanent
            # ledger. `clock_stamp` returns the machine reading unchanged
            # unless the ledger PROVES the clock is behind, in which case it
            # returns the newest recorded timestamp as a floor. It writes and
            # annotates; it never refuses (a raise here would lose every
            # remaining substrate write the fire owes).
            # The RAW machine reading, kept in a name. CLOCKTS1 judges a
            # caller-supplied `ts` against THIS, never against `clock_stamp`:
            # the floor is exactly the value a poisoned ledger moves, so
            # measuring a suspicious lead against it would let a contaminated
            # workspace excuse the row that contaminated it.
            machine_now = _dt.datetime.now(_dt.timezone.utc)
            clock_stamp = _clock1_floor_stamp(
                machine_now, existing_newest_ts, events_path=path)
            now_iso = clock_stamp["ts"].isoformat()
            if clock_stamp.get("refused"):
                # CLOCKTS1 — a floor correction was available and was REFUSED
                # because the ledger maximum is a forward-dated row, not clock
                # evidence. Said out loud: silence here is how the field case
                # ran for eighteen days without anyone noticing.
                import sys as _sys
                _sys.stderr.write(
                    f"[atomic_append_jsonl] substrate clock floor refused "
                    f"({clock_stamp['refused']}): the newest ledger timestamp "
                    f"leads the .seqhw witness, so it is a forward-dated row "
                    f"rather than proof this clock is behind. Stamped the "
                    f"machine reading (holder={holder}). Run "
                    f"shared/scripts/repair_future_ts.py to see what is "
                    f"recoverable.\n"
                )
            # WHICH CLOCK A CALLER'S `ts` IS JUDGED AGAINST (fix round, F-2).
            # The raw machine reading is the right reference in general — a
            # contaminated workspace must not be allowed to excuse the row that
            # contaminated it. But on the exact incident this module exists for
            # — a machine that booted unsynced and reads days behind — "the
            # real time" IS ahead of the raw clock, by precisely the skew, so a
            # caller passing a meeting's CORRECT start time was refused at 25h
            # behind. The discriminator tolerates that same machine's skew up
            # to four days; the gate refusing it at one was the two bounds
            # disagreeing about one clock.
            #
            # So: when a floor was actually ADOPTED, the corroborated stamp is
            # this machine's best reading of now and is the reference. When it
            # was not — including every refusal, which is what a poisoned
            # workspace now produces — `ts_source` is None and the reference
            # stays the raw clock. The anti-excuse property is untouched.
            ts_reference = (clock_stamp["ts"] if clock_stamp["ts_source"]
                            else machine_now)
            ts_refused: list[tuple[int, str, float]] = []
            for ev in evs:
                current_seq = ev.get("seq")
                seq_is_valid_human_counter = (
                    isinstance(current_seq, (int, float))
                    and not isinstance(current_seq, bool)
                    and current_seq < EPOCH_THRESHOLD
                )
                if current_seq is None or not isinstance(current_seq, (int, float)) or isinstance(current_seq, bool):
                    ev["seq"] = next_seq_val
                    next_seq_val += 1
                elif (
                    seq_is_valid_human_counter
                    and int(current_seq) >= next_seq_val
                    and int(current_seq) > existing_max_seq + SEQ_GAP_MAX
                ):
                    # BUG-8330 item 8 — explicit seq LEADS the ledger by more
                    # than SEQ_GAP_MAX: almost certainly a hand-stamped
                    # artifact (the 999999 case), and honoring it relocates
                    # the allocation ceiling permanently (max+1 allocators +
                    # the .seqhw sidecar lock the jump in). Reassign like a
                    # stale seq, loudly — the write itself is preserved.
                    import sys as _sys
                    _sys.stderr.write(
                        f"[atomic_append_jsonl] explicit seq {int(current_seq)} "
                        f"leads the ledger max ({existing_max_seq}) by more than "
                        f"SEQ_GAP_MAX={SEQ_GAP_MAX}; reassigned to {next_seq_val} "
                        f"(holder={holder}). Never hand-stamp seq — omit it and "
                        "the appender allocates inside the writer lock.\n"
                    )
                    ev["seq"] = next_seq_val
                    next_seq_val += 1
                elif seq_is_valid_human_counter and int(current_seq) >= next_seq_val:
                    # Explicit human-counter seq in this batch — bump counter past it
                    # so subsequent missing-seq events stamp monotonically. Nano-epoch
                    # seqs are NOT considered (would jump next_seq to 1.77e18+1).
                    next_seq_val = int(current_seq) + 1
                elif seq_is_valid_human_counter and int(current_seq) < next_seq_val:
                    # Explicit but STALE human-counter seq — a value the caller
                    # peeked before a concurrent append overtook it. Honoring it
                    # would write a DUPLICATE seq, corrupting supersedes_seq /
                    # source_event_seq / _commitment_id back-references (and letting
                    # one resolution close two commitments). Reassign it like a
                    # missing seq so the ledger stays monotonic + collision-free
                    # (deep-audit 2026-05-29, finding #7).
                    ev["seq"] = next_seq_val
                    next_seq_val += 1
                current_ts = ev.get("ts")
                if current_ts is None or not isinstance(current_ts, str) or not current_ts.strip():
                    ev["ts"] = now_iso
                    if clock_stamp["ts_source"]:
                        # The contamination trail (CLOCK1 D4), additive so
                        # every existing reader is unaffected: what the stamp
                        # came from, and what the machine actually said.
                        ev["ts_source"] = clock_stamp["ts_source"]
                        ev["machine_ts"] = clock_stamp["machine_ts"]
                else:
                    # A caller-supplied non-empty `ts` is still never REWRITTEN
                    # — historic backfills are legal and indistinguishable from
                    # intent, and `ts` = meeting start time is a live data
                    # contract that dormancy and "when did I last meet with X"
                    # read. CLOCKTS1 changes nothing about a backward `ts`.
                    #
                    # A FORWARD one is different in kind: it is a claim about
                    # something that has not happened, it becomes the ledger
                    # maximum, and every window in the product reads it. Bound
                    # it exactly the way an explicit `seq` above is bounded.
                    lead = _ts_lead_seconds(current_ts, ts_reference)
                    if lead is not None and lead > TS_LEAD_MAX_SECONDS:
                        ts_refused.append((len(ts_refused), current_ts, lead))

            # CLOCKTS1 — REFUSE INTO REVIEW. Neither of the two silent outcomes
            # is acceptable here: re-stamping the row loses the caller's stated
            # time with no trail (and the caller may have meant it — a genuinely
            # scheduled item belongs in the payload, not in `ts`), and accepting
            # it writes a future date into the permanent ledger that every
            # window, the dormancy math and the confirmed-tier age-out will read
            # as fact. So the batch goes to a REVIEW side file with a named
            # reason, exactly like the FS-04 quarantine below, and the caller
            # hears about it.
            #
            # The WHOLE batch is set aside, not just the offending rows: a batch
            # is often referentially linked (a commitment and the closure that
            # resolves it), and splitting it would land half a relationship.
            if ts_refused and os.environ.get("CR_TS_LEAD_GUARD", "1") != "0":
                stamp = machine_now.strftime("%Y%m%dT%H%M%S%fZ")
                r_path = path.with_name(path.name + f".tsreview-{stamp}.jsonl")
                r_lines = "".join(
                    json.dumps(e, ensure_ascii=False) + "\n" for e in evs)
                try:
                    r_existing = (r_path.read_text(encoding=encoding)
                                  if r_path.exists() else "")
                    if r_existing and not r_existing.endswith("\n"):
                        r_existing += "\n"
                    atomic_write_text(r_path, r_existing + r_lines,
                                      encoding=encoding)
                except Exception:
                    pass
                marker = path.with_name(path.name + ".tsreview.json")
                worst = max(ts_refused, key=lambda r: r[2])
                try:
                    atomic_write_json(marker, {
                        "detected": machine_now.isoformat(),
                        "reason": "caller_ts_leads_machine_clock",
                        "n_refused": len(ts_refused),
                        "n_in_batch": len(evs),
                        "worst_ts": worst[1],
                        "worst_lead_seconds": worst[2],
                        "ts_lead_max_seconds": TS_LEAD_MAX_SECONDS,
                        "machine_now": machine_now.isoformat(),
                        "reference": ts_reference.isoformat(),
                        "reference_kind": ("substrate_floor"
                                           if clock_stamp["ts_source"]
                                           else "machine_clock"),
                        "review_path": str(r_path),
                        "holder": holder,
                    })
                except Exception:
                    pass
                raise FutureTimestampRefused(
                    f"refused {len(ts_refused)} event(s) whose caller-supplied "
                    f"`ts` leads this workspace's best reading of now by more "
                    f"than TS_LEAD_MAX_SECONDS={TS_LEAD_MAX_SECONDS}s (worst: "
                    f"{worst[1]!r}, {worst[2]:.0f}s ahead of "
                    f"{ts_reference.isoformat()}; machine clock reads "
                    f"{machine_now.isoformat()}; holder={holder}). The batch of "
                    f"{len(evs)} was set aside for review at {r_path.name} — "
                    f"nothing was re-stamped and nothing was written to the "
                    f"ledger. A future date in `ts` becomes the ledger maximum "
                    f"and is read as fact by every window, by dormancy, and by "
                    f"the confirmed-tier age-out. If the event has already "
                    f"happened, pass its real time; if it has not, omit `ts` "
                    f"and put the scheduled time in the payload."
                )

        stamped[:] = evs
        new_lines = "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in evs)

        # FS-04 — seq high-water regression guard. If the file on disk regressed
        # below the recorded high-water (a stale lineage clobbered it via Drive
        # last-writer-wins), REFUSE the in-place append (writing it would append
        # to the stale lineage and let the clobber win again), QUARANTINE the
        # batch to a side file so nothing is lost, drop a LOUD marker, and RAISE.
        # CR_SEQ_HIGHWATER=0 disables (emergencies / intentional resets).
        if hw_guard_on:
            # `hw` was read once, above, alongside the tail (INDEX1 Phase A);
            # `existing_max_seq` is the tail max, promoted to the full-file max
            # whenever the tail disagreed with the sidecar — so this comparison
            # keeps its exact meaning: the FILE regressed below the high-water.
            # No `path.exists()` here (second-eyes fix 2026-07-20): an ABSENT
            # events.jsonl with a live .seqhw is the most-regressed view possible
            # (a partial mount) and is never legitimate — rotation always leaves
            # the active file in place with its seq-continuity marker. Writing
            # through it would start a fresh seq-1 lineage and the high-water
            # advance below would silently LOWER .seqhw. Refuse + quarantine.
            if hw is not None and existing_max_seq < hw:
                import datetime as _dt
                stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
                q_path = path.with_name(path.name + f".quarantine-{stamp}.jsonl")
                try:
                    q_existing = q_path.read_text(encoding=encoding) if q_path.exists() else ""
                    if q_existing and not q_existing.endswith("\n"):
                        q_existing += "\n"
                    atomic_write_text(q_path, q_existing + new_lines, encoding=encoding)
                except Exception:
                    pass
                marker = path.with_name(path.name + ".seqregression.json")
                try:
                    atomic_write_json(marker, {
                        "detected": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                        "file_max_seq": existing_max_seq,
                        "sidecar_max_seq": hw,
                        "n_quarantined": len(evs),
                        "quarantine_path": str(q_path),
                        "holder": holder,
                    })
                except Exception:
                    pass
                raise SubstrateRegressionError(
                    f"events.jsonl regressed below the seq high-water "
                    f"(on-disk max seq {existing_max_seq} < recorded {hw}) — a "
                    f"stale lineage clobbered the live log. Refused the in-place "
                    f"append; quarantined {len(evs)} event(s) to {q_path.name}. "
                    f"Recover the live log (Drive version history + merge) before "
                    f"re-firing; see the substrate-regression alarm in a health "
                    f"check or the morning brief."
                )

        if not is_events:
            atomic_write_text(path, existing + new_lines, encoding=encoding)
        else:
            # INDEX1 Phase A (R4/R5) — TRUE APPEND. The ledger is opened
            # O_APPEND in binary mode and the batch lands as one write + fsync.
            # Nothing before the appended bytes is touched, ever.
            assert tail is not None
            lines_bytes = new_lines.encode(encoding)
            payload = lines_bytes
            if tail.size > 0 and not tail.ends_with_newline:
                # TORN TAIL (R4 / SPEC D7). The file does not end in a newline:
                # a crash or a mid-append sync left a partial last line. Never
                # truncate — history is additive. Lead the batch with a newline
                # so the fragment stays a complete, skippable junk line (the
                # defensive readers already tolerate interior junk), and put
                # the heal ON THE RECORD through the FS-15 read-alarm sidecar
                # — the same sidecar `events_io._iter_file` uses for the
                # truncation signature — so it surfaces in system-health and
                # the brief instead of vanishing. Recorded in BOTH shapes: a
                # fragment that will not parse (a mid-append crash or sync
                # flush) and an unterminated line that does parse (a hand
                # edit); this appender always terminates its rows, so either
                # shape means something other than the appender wrote here.
                payload = b"\n" + lines_bytes
                frag = tail.raw_all.rsplit(b"\n", 1)[-1]
                frag_parses = False
                try:
                    frag_parses = isinstance(
                        json.loads(frag.decode(encoding, errors="replace")),
                        dict)
                except (ValueError, TypeError):
                    frag_parses = False
                try:
                    from read_alarm import record_read_alarm
                except ImportError:
                    import sys as _sys
                    _sys.path.insert(0, str(Path(__file__).resolve().parent))
                    try:
                        from read_alarm import record_read_alarm
                    except Exception:
                        record_read_alarm = None
                except Exception:
                    record_read_alarm = None
                if record_read_alarm is not None:
                    shape = ("unparseable fragment" if not frag_parses
                             else "parseable row missing its terminator")
                    record_read_alarm(
                        path,
                        f"torn tail healed by appender ({shape}): {len(frag)} "
                        f"unterminated byte(s) kept as a skippable line "
                        f"before appending {len(evs)} event(s)",
                        reader=f"atomic_append_jsonl:{holder}",
                    )
            _append_bytes(path, payload)

            # R4 — READ BACK before trusting the write. On POSIX a rotation or
            # repair that replaced the file between our open and our write
            # leaves the bytes on an orphaned inode; on any platform a write
            # that did not land must not advance `.seqhw`. Re-open, read a
            # window at least as large as the batch, and require (a) the
            # batch bytes to be PRESENT in the live file's tail and (b) the
            # file's last complete line to parse to the stamped seq. (b) is
            # the contract; (a) is what decides between "our write never
            # landed" (raise, side-file, do not advance) and "something else
            # wrote after us outside the lock" (the batch IS in the ledger, so
            # raising would set aside a duplicate — say so on stderr instead).
            verify = _read_events_tail(
                path, encoding=encoding,
                max_bytes=max(_TAIL_READ_BYTES, len(lines_bytes) + 1024))
            landed = lines_bytes in verify.raw_all
            last_seq_expected = evs[-1].get("seq") if evs else None
            last_line_ok = False
            try:
                last_line = verify.raw_all.rstrip(b"\n").rsplit(b"\n", 1)[-1]
                parsed_last = json.loads(last_line.decode(encoding))
                last_line_ok = (isinstance(parsed_last, dict)
                                and parsed_last.get("seq") == last_seq_expected)
            except (ValueError, TypeError):
                last_line_ok = False
            if landed and not last_line_ok:
                import sys as _sys
                _sys.stderr.write(
                    f"[atomic_append_jsonl] read-back: the batch landed but "
                    f"the last line of {path.name} is not its final row (seq "
                    f"{last_seq_expected}) — another writer appended OUTSIDE "
                    f"the writer lock (holder={holder}).\n"
                )
            if not landed:
                import datetime as _dt
                stamp = _dt.datetime.now(_dt.timezone.utc).strftime(
                    "%Y%m%dT%H%M%S%fZ")
                side = path.with_name(path.name + f".appendverify-{stamp}.jsonl")
                try:
                    atomic_write_text(side, new_lines, encoding=encoding)
                except Exception:
                    pass
                try:
                    atomic_write_json(
                        path.with_name(path.name + ".appendverify.json"), {
                            "detected": _dt.datetime.now(
                                _dt.timezone.utc).isoformat(),
                            "reason": "batch_not_at_file_tail_after_append",
                            "n_events": len(evs),
                            "side_file": str(side),
                            "holder": holder,
                        })
                except Exception:
                    pass
                raise AppendVerificationError(
                    f"appended {len(evs)} event(s) but the read-back does not "
                    f"find them at the end of {path.name} — the file was "
                    f"replaced under the writer (rotation / repair outside the "
                    f"lock, or a sync swap). The batch is preserved at "
                    f"{side.name}; .seqhw was NOT advanced (holder={holder})."
                )

        # FS-04 — advance the high-water mark AFTER a clean, verified write.
        if hw_guard_on:
            new_max = max((int(e["seq"]) for e in evs
                           if isinstance(e.get("seq"), (int, float))
                           and not isinstance(e.get("seq"), bool)
                           and e["seq"] < 10**10), default=existing_max_seq)
            target = max(new_max, existing_max_seq, hw if hw is not None else 0)
            if _write_seqhw(path, target):
                # A successful advance retires any pending rescan marker: the
                # sidecar is the file's max again.
                if rescan:
                    _clear_rescan_marker(path)
            else:
                # R2 — never silent. Marker or raise; the batch is on disk.
                _note_seqhw_write_failed(path, target, holder)

        # SPEC A3 — maintain the source_ref dedup index while still holding the
        # writer lock (events branch only). Best-effort: an index failure must
        # NEVER fail the event write. Mirrors the auto-stamp caller-agnostic
        # pattern — LLM-driven writers can't forget the index because they never
        # touch it. path = <ws>/_hq/data/events.jsonl → workspace root is 3 up.
        if is_events:
            try:
                from source_ref_index import record_keys
            except ImportError:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).resolve().parent))
                from source_ref_index import record_keys
            try:
                record_keys(path.parent.parent.parent, evs)
            except Exception:
                pass

            # SPEC EVT1 — WARN-ONLY payload validation. Surfaces payload drift on
            # stderr for the burn-in; NEVER blocks the write (promotion to
            # blocking is a later release after zero-warning burn-in). Disable
            # with CR_PAYLOAD_CHECK=0.
            import os as _os
            if _os.environ.get("CR_PAYLOAD_CHECK", "1") != "0":
                try:
                    from event_payload_check import check_payload
                    import sys as _sys
                    for _e in evs:
                        _viol = check_payload(_e)
                        if _viol:
                            _sys.stderr.write("[event_payload] " + "; ".join(_viol) + "\n")
                except Exception:
                    pass

    if is_events:
        # SPEC A1 — serialize the whole read->stamp->rename behind the writer
        # lock so concurrent appends can't lose an event or duplicate a seq.
        # Lazy import avoids an import cycle (writer_lock imports atomic_write).
        try:
            from writer_lock import events_writer_lock
        except ImportError:
            import sys as _sys
            _sys.path.insert(0, str(Path(__file__).resolve().parent))
            from writer_lock import events_writer_lock
        with events_writer_lock(path, holder=holder):
            # SPEC SYNC1 A3 — quarantine auto-reconcile + fail-closed merge-
            # forward, INSIDE the lock, BEFORE the FS-04 check below. Fast no-op
            # unless a marker / quarantine / Drive conflict-copy is present, so
            # the normal append path pays nothing. On a healthy view it replays
            # any quarantined batches (self-heal — zero manual recovery); on a
            # regressed view it merges forward ONLY when a candidate at/above the
            # high-water is visible, and otherwise does nothing so the FS-04
            # guard in _read_stamp_write stays the last-resort fail-closed floor
            # (the load-bearing rule: a stale sandbox mount must never let the
            # reconciler promote the stale copy and become the clobberer itself).
            # Best-effort: a reconcile failure must never block the caller's
            # write — the FS-04 guard still protects the substrate.
            if os.environ.get("CR_RECONCILE_FORWARD", "1") != "0":
                try:
                    from reconcile_forward import reconcile_forward
                except ImportError:
                    import sys as _sys
                    _sys.path.insert(0, str(Path(__file__).resolve().parent))
                    try:
                        from reconcile_forward import reconcile_forward
                    except Exception:
                        reconcile_forward = None
                except Exception:
                    reconcile_forward = None
                if reconcile_forward is not None:
                    try:
                        reconcile_forward(path, holder=holder)
                    except Exception:
                        pass
            _read_stamp_write(events)
    else:
        _read_stamp_write(events)
    return stamped


def acquire_write_lock(
    path: str | Path,
    holder: str = "unknown",
    timeout_s: float = 10.0,
    stale_after_s: float = 60.0,
) -> Path:
    """Acquire a cooperative cross-process write lock on `path` (v3.13.0+).

    NEW CODE: for events.jsonl use `writer_lock.events_writer_lock` (SPEC A1) —
    an OS byte-range lock that the kernel releases on crash. This sentinel
    variant remains the canonical lock for entities.json / aliases.json (its
    mtime-staleness + mv-aside semantics are load-bearing for those callers).

    Creates a sentinel file at `{path}.lock` containing the current PID +
    timestamp + caller-provided holder name. If the lock file already exists
    AND is fresh (mtime < stale_after_s ago), waits up to `timeout_s` for the
    holder to release; if still locked after that, raises TimeoutError.

    A "stale" lock file (older than stale_after_s) is assumed to be from a
    crashed writer and is reclaimed automatically — this prevents permanent
    deadlock if a skill crashes mid-write.

    Returns the lock-file Path so the caller can release it via
    release_write_lock(lock_path).

    Use this BEFORE read-modify-write on entities.json / aliases.json. Atomic
    rename alone doesn't prevent two concurrent read-modify-writes from
    overwriting each other's changes (last-writer-wins, silent data loss);
    this lock does.

    For append-only writes to events.jsonl, atomic_append_jsonl already does
    a full read+rewrite per call, so concurrent appends are rare-but-possible
    races; if you have many events to append, BATCH them into one call rather
    than calling per event under separate locks.

    Cooperative-only: only protects against writers that also call this
    helper. If a script does direct `path.write_text(...)`, this lock won't
    stop them. Every canonical writer in shared/scripts/ goes through this.

    Background on why a sentinel file vs. fcntl/flock: fcntl is POSIX-only
    (no Windows). msvcrt.locking is Windows-only. portalocker would work
    but adds a dependency. A sentinel file is portable, simple, and good
    enough for the actual concurrency volume we see (a few skills hitting
    the same file within seconds, not high-volume contention).
    """
    import datetime
    import os as _os
    import time as _time

    path = Path(path)
    lock_path = path.parent / (path.name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)

    start = _time.monotonic()
    while True:
        # If lock exists, check whether it's stale
        if lock_path.exists():
            try:
                mtime = lock_path.stat().st_mtime
                age = _time.time() - mtime
            except OSError:
                # Race: file disappeared between exists() and stat(). Retry the loop.
                continue
            # BUG-8330 item 7d — REFUSE the mtime-stale reclaim while the
            # holder pid is provably ALIVE on this machine. The mtime backstop
            # exists for crashed writers; a live writer mid-way through a slow
            # multi-MB append is not crashed, and reclaiming its lock is
            # exactly the duplicate-seq / lost-event race the lock closes.
            # A dead or unreadable pid (crash, other machine) keeps today's
            # reclaim behavior.
            holder_alive = False
            if age > stale_after_s:
                try:
                    lock_pid, _epoch = _read_lock_payload(lock_path)
                    holder_alive = bool(lock_pid) and _pid_alive(lock_pid)
                except Exception:
                    holder_alive = False
            if age > stale_after_s and not holder_alive:
                # Stale — reclaim. Best-effort; if another writer grabs it
                # first, we'll loop again on our next attempt. v4.8.1 (F-11):
                # reclaim goes through _clear_lock_file so a refused unlink
                # (cloud-sync client briefly holding the file) mv-asides
                # instead of crashing the writer; if even the rename fails,
                # fall into the timeout below rather than spinning forever.
                if not _clear_lock_file(lock_path):
                    if _time.monotonic() - start > timeout_s:
                        raise TimeoutError(
                            f"Write lock on {path.name} is stale but cannot be "
                            f"cleared (unlink and rename both refused by the "
                            f"mount). Waited {timeout_s:.1f}s."
                        )
                    _time.sleep(0.1)
                    continue
            else:
                # Fresh lock held by someone else. Wait + retry.
                if _time.monotonic() - start > timeout_s:
                    try:
                        held_by = lock_path.read_text(encoding="utf-8").strip()
                    except OSError:
                        held_by = "(unreadable)"
                    raise TimeoutError(
                        f"Write lock on {path.name} is held by another process: {held_by}. "
                        f"Waited {timeout_s:.1f}s. Retry, or check for hung writers."
                    )
                _time.sleep(0.1)
                continue

        # Try to atomically create the lock file. O_EXCL fails if another
        # process beat us to it; that's the "I lost the race" signal.
        try:
            fd = _os.open(
                str(lock_path),
                _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            # Another writer grabbed the lock between our check and create. Retry.
            continue
        # Write metadata. If this fails, drop the lock so we don't hang others.
        try:
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                payload = {
                    "pid": _os.getpid(),
                    "holder": holder,
                    "acquired_at": datetime.datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(payload))
        except Exception:
            try:
                lock_path.unlink()
            except OSError:
                pass
            raise
        return lock_path


def _clear_lock_file(lock_path: Path, unlink_retries: int = 3,
                     retry_delay_s: float = 0.05) -> bool:
    """Remove a lock sentinel, tolerating cloud-sync mounts (v4.8.1, F-11).

    Unlink with brief retries first, mv-aside second. Returns True when the
    lock file is out of the way (deleted, renamed, or already gone), False
    only when both unlink and rename are refused.

    WHY THE RETRIES (F-11, integration-2026-07): the seven
    `entities.json.lock.stale.*` files from one multi-write session were NOT
    crashed writers — every write succeeded. They were mv-aside litter: on a
    Drive-synced workspace the sync client briefly holds each freshly-created
    lock file, the release-time unlink gets OSError-refused, and the old code
    renamed immediately. The hold is transient (ms), so a couple of short
    retries clears almost every case without littering. The mv-aside stays as
    the backstop; cleanup Rule 9 (`cleanup_actions.sweep_stale_locks`, weekly)
    archives whatever still lands.
    """
    import os as _os
    import time as _time

    for attempt in range(max(1, unlink_retries)):
        try:
            lock_path.unlink()
            return True
        except FileNotFoundError:
            return True  # someone else cleared it; done
        except OSError:
            if attempt + 1 < max(1, unlink_retries):
                _time.sleep(retry_delay_s)
    # Unlink keeps getting refused — mv-aside with the deterministic
    # stale-name (`<file>.lock.stale.<epoch>.<pid>`, the exact pattern the
    # weekly sweep archives into _archive/stale-locks/).
    try:
        stale_path = lock_path.with_suffix(
            lock_path.suffix + f".stale.{int(_time.time())}.{_os.getpid()}"
        )
        _os.rename(str(lock_path), str(stale_path))
        return True
    except OSError:
        return False


def release_write_lock(lock_path: Path) -> None:
    """Release a write lock acquired via acquire_write_lock. Idempotent —
    silently no-ops if the lock file is already gone (someone else reclaimed
    it as stale, or this is a second release call).

    v3.13.8: handles sandbox-mount OSError on unlink via mv-aside fallback
    (Bug #21 — Drive-mounted workspaces sometimes refuse unlink but permit
    rename). v4.8.1 (F-11): unlink is retried briefly before the mv-aside so
    a transient sync-client hold doesn't litter a `.stale.*` file per write;
    leftover `.stale.*` files are archived by cleanup Rule 9's weekly sweep.
    If even the rename fails, this is a release-time non-fatal; the lock will
    eventually be reclaimed as stale by acquire_write_lock.
    """
    _clear_lock_file(lock_path)


# ---------------------------------------------------------------------------
# Multi-write context manager (v3.13.8 — Bug #20 + #18 + #21)
# ---------------------------------------------------------------------------

class AtomicWriteLockError(RuntimeError):
    """Raised by multi_write_context when the lock cannot be acquired."""


def _read_lock_payload(lock_path: Path) -> tuple[int, float]:
    """Best-effort read of (pid, acquired_at_epoch) from a lock sentinel.

    Returns (0, 0.0) when the lock is unreadable, corrupted, or missing
    expected fields — the caller treats that as a corrupt lock and reclaims.
    """
    import datetime as _dt

    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0, 0.0
    if not raw:
        return 0, 0.0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return 0, 0.0
    if not isinstance(payload, dict):
        return 0, 0.0
    pid = payload.get("pid")
    if not isinstance(pid, int):
        pid = 0
    acquired_at = payload.get("acquired_at")
    epoch = 0.0
    if isinstance(acquired_at, str):
        try:
            epoch = _dt.datetime.fromisoformat(acquired_at).timestamp()
        except ValueError:
            epoch = 0.0
    return pid, epoch


def _pid_alive_windows(pid: int) -> bool:
    """Windows pid-liveness via OpenProcess + WaitForSingleObject.

    A running process's handle stays unsignalled, so WaitForSingleObject(h, 0)
    returns WAIT_TIMEOUT. Once it exits, the handle signals and we get
    WAIT_OBJECT_0. That distinction is the liveness answer.

    Deliberately NOT using GetExitCodeProcess/STILL_ACTIVE: a process that exits
    with code 259 is indistinguishable from one still running.
    """
    import ctypes
    from ctypes import wintypes

    SYNCHRONIZE = 0x00100000
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    WAIT_TIMEOUT = 0x00000102
    ERROR_ACCESS_DENIED = 5

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # Explicit prototypes are mandatory: ctypes defaults restype to c_int, which
    # truncates a 64-bit HANDLE and yields a bogus handle on win64.
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(
        SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, pid
    )
    if not handle:
        # Access-denied means the pid exists but belongs to another user or a
        # protected process — same call as the POSIX PermissionError branch:
        # it's alive, just not ours. Anything else (chiefly
        # ERROR_INVALID_PARAMETER) means no such process.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def _pid_alive(pid: int) -> bool:
    """Best-effort pid-liveness check that works on Windows and POSIX.

    WINDOWS MUST NOT GO THROUGH os.kill (found 2026-07-15).

    `os.kill(pid, 0)` is the standard POSIX liveness idiom: signal 0 is a no-op
    probe that checks existence and sends nothing. On Windows it means something
    else entirely. CPython's os.kill special-cases the signal value BEFORE it
    ever reaches TerminateProcess:

        if (signal == CTRL_C_EVENT || signal == CTRL_BREAK_EVENT)
            err = GenerateConsoleCtrlEvent((DWORD)signal, (DWORD)pid);

    and on Windows CTRL_C_EVENT == 0. So `os.kill(pid, 0)` does not probe the
    process — it broadcasts a real Ctrl+C to the console process group. The call
    returns normally (so the old code reported "alive"), and the Ctrl+C lands
    asynchronously a beat later, killing whatever shares that console: the test
    runner, a CI job, the operator's terminal.

    Observed: tests/run_all.py died with KeyboardInterrupt inside
    WaitForSingleObject on 4/4 runs, always at run_atomic_write_multi_test.py —
    the only suite reaching this path — with nobody at the keyboard. The
    traceback was indistinguishable from a human pressing Ctrl+C, because it is
    the same signal.

    The no-console case is no better: GenerateConsoleCtrlEvent fails, OSError is
    raised, the old `except OSError: return False` reports "dead" — for every pid
    it is ever asked about. A live writer's lock then reads as abandoned and gets
    reclaimed, which is precisely what the lock exists to prevent.

    Regression coverage: tests/run_pid_alive_windows_test.py
    """
    import os as _os
    import sys as _sys

    if pid <= 0:
        return False

    if _sys.platform == "win32":
        return _pid_alive_windows(pid)

    try:
        _os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Pid exists but is owned by another user / system process — treat as alive.
        return True
    except OSError:
        return False


from contextlib import contextmanager


@contextmanager
def multi_write_context(
    workspace_path,
    holder: str = "multi_write",
    timeout_s: float = 60.0,
    stale_after_s: float = 300.0,
):
    """Hold a single shared write lock across multiple writes in the same
    process (v3.13.8+ — Bug #20 multi-write deadlock fix).

    The classic pattern of calling atomic_write_json_locked() N times in a row
    in the same process can deadlock on Drive-synced workspaces when the prior
    release hasn't propagated to the filesystem view yet. multi_write_context
    grabs ONE lock at entry, holds it across all writes, and releases ONCE at
    exit. Bundles three Bug fixes in one helper:

      - Bug #20: same-process multi-write deadlock (one lock, not N)
      - Bug #18: stale-lock auto-reclaim (pid-liveness OR time-based fallback)
      - Bug #21: sandbox mount unlink-fail (mv-aside fallback per
        release_write_lock above)

    Args:
      workspace_path: workspace root (the directory containing _hq/).
      holder: caller name (e.g. "command-room-update-bridge").
      timeout_s: max seconds to wait if lock is held by ANOTHER live writer.
      stale_after_s: lock files older than this are considered abandoned and
        reclaimed even if their PID still appears alive on the system. 5min
        default (matches the architectural-review spec). The pid-liveness
        check fires first; this is the belt-and-suspenders backstop.

    Usage in update-bridge migration:

        with multi_write_context(workspace_path, holder="update-bridge"):
            run_corruption_recovery_if_needed()
            apply_canonical_surname_fix()
            prompt_brain_name_if_missing()
            # ...all writes under single lock — no deadlock, no partial state

    Raises:
      AtomicWriteLockError: if the lock cannot be acquired within timeout_s
        and the existing holder is genuinely alive + recent.
    """
    import os as _os
    import time as _time

    workspace_path = Path(workspace_path)
    lock_dir = workspace_path / "_hq" / ".system"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "atomic.lock"

    pid = _os.getpid()
    acquired = False
    existing_pid = 0
    start = _time.monotonic()

    while not acquired:
        try:
            fd = _os.open(
                str(lock_path),
                _os.O_WRONLY | _os.O_CREAT | _os.O_EXCL,
                0o600,
            )
            with _os.fdopen(fd, "w", encoding="utf-8") as f:
                import datetime as _dt
                payload = {
                    "pid": pid,
                    "holder": holder,
                    "acquired_at": _dt.datetime.now().isoformat(timespec="seconds"),
                }
                f.write(json.dumps(payload))
            acquired = True
            break
        except FileExistsError:
            existing_pid, lock_epoch = _read_lock_payload(lock_path)
            if existing_pid == 0 and lock_epoch == 0.0:
                # Corrupt / unreadable lock — reclaim. v4.8.1 (F-11, second-eyes
                # finding 1): a reclaim whose unlink AND rename are both refused
                # must fall into the timeout, not spin forever.
                if not _clear_lock_file(lock_path):
                    if _time.monotonic() - start > timeout_s:
                        raise AtomicWriteLockError(
                            f"Could not clear corrupt multi_write lock at "
                            f"{lock_path} after {timeout_s:.0f}s (unlink and "
                            f"rename both refused by the mount)."
                        )
                    _time.sleep(1.0)
                continue

            now = _time.time()
            stale_by_time = (now - lock_epoch) > stale_after_s
            alive = _pid_alive(existing_pid)

            if not alive or stale_by_time:
                # Reclaim a dead-pid or time-stale lock (same F-11 contract:
                # unclearable → timeout, never an infinite loop).
                if not _clear_lock_file(lock_path):
                    if _time.monotonic() - start > timeout_s:
                        raise AtomicWriteLockError(
                            f"Stale multi_write lock at {lock_path} cannot be "
                            f"cleared after {timeout_s:.0f}s (unlink and rename "
                            f"both refused by the mount); holder "
                            f"pid={existing_pid} is dead or expired."
                        )
                    _time.sleep(1.0)
                continue

            if _time.monotonic() - start > timeout_s:
                raise AtomicWriteLockError(
                    f"Could not acquire multi_write lock on {lock_path} after "
                    f"{timeout_s:.0f}s; holder pid={existing_pid} appears alive."
                )
            _time.sleep(1.0)

    try:
        yield lock_path
    finally:
        release_write_lock(lock_path)


def atomic_write_json_locked(
    path: str | Path,
    data: Any,
    holder: str = "unknown",
    indent: int | None = 2,
    ensure_ascii: bool = False,
    timeout_s: float = 10.0,
    verify_parse: bool = True,
) -> None:
    """Atomic JSON write WITH cross-process lock + post-write parse check.

    The canonical writer for entities.json / aliases.json (v3.13.0+). Three
    guarantees on top of atomic_write_json:

      1. **Cross-process lock**: acquires a sentinel lock before writing, so
         two skills writing concurrently can't overwrite each other's
         changes. Releases the lock after write completes (or fails).

      2. **Post-write parse check** (when verify_parse=True): re-reads the
         file after write and confirms it parses as JSON. If it doesn't,
         attempts to restore from the newest backup in `_backups/` and
         raises so the caller knows the write failed.

      3. **Caller identification**: `holder` is written into the lock file
         and into any lock-conflict error so debugging concurrent-writer
         issues is easier. Pass the calling skill name (e.g.
         holder="people-crm").

    Use this for ANY write to a substrate file where:
      - the file is read-modify-written (concurrent writers can race)
      - downstream readers depend on the file being valid JSON

    For append-only events.jsonl: prefer atomic_append_jsonl directly
    (it's append-only so the lock helps less, and the read+rewrite cost
    is already paid).
    """
    path = Path(path)
    lock_path = acquire_write_lock(path, holder=holder, timeout_s=timeout_s)
    try:
        atomic_write_json(path, data, indent=indent, ensure_ascii=ensure_ascii)
        if verify_parse:
            try:
                content = path.read_text(encoding="utf-8")
                json.loads(content)  # raises if invalid
            except (OSError, json.JSONDecodeError) as e:
                _try_restore_from_backup(path)
                raise RuntimeError(
                    f"Post-write parse check failed on {path.name}: {e}. "
                    f"Restored from newest backup (if any). The write was rejected."
                ) from e
        # SPEC SYNC1 D-4 — freshness sidecar for name-bearing substrate.
        # entities.json / aliases.json have no seq semantics, so the seqhw guard
        # can't cover them; sighting #2 (2026-07-19) was a transient entities
        # parse failure in a scheduled fire. A tiny `<file>.rev` sidecar stamped
        # at this chokepoint lets a reader notice it is looking at an older view
        # than the last locked write produced. Best-effort — the write is
        # already durable; a rev-stamp failure must never fail the write.
        _stamp_rev_sidecar(path)
    finally:
        release_write_lock(lock_path)


_REV_SIDECAR_NAMES = ("entities.json", "aliases.json")


def _rev_sidecar_path(path: Path) -> Path:
    return path.with_name(path.name + ".rev")


def _stamp_rev_sidecar(path: Path) -> None:
    """Bump `<file>.rev` = {rev, updated} after a locked write to entities.json /
    aliases.json (SPEC SYNC1 D-4). rev is a monotonic counter read from the
    prior sidecar (absent/corrupt → start at 1). No-op for other files."""
    if path.name not in _REV_SIDECAR_NAMES:
        return
    try:
        import datetime as _dt
        prior = 0
        sp = _rev_sidecar_path(path)
        try:
            raw = json.loads(sp.read_text(encoding="utf-8"))
            v = raw.get("rev")
            if isinstance(v, int) and not isinstance(v, bool) and v >= 0:
                prior = v
        except (OSError, json.JSONDecodeError, AttributeError, ValueError):
            prior = 0
        atomic_write_json(sp, {
            "rev": prior + 1,
            "updated": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        })
    except Exception:
        pass


def read_rev_sidecar(path: str | Path) -> dict | None:
    """The `{rev, updated}` freshness sidecar for entities.json / aliases.json,
    or None when absent/corrupt (SPEC SYNC1 D-4). Back-compat: a workspace with
    no sidecar reads as None — readers must treat that as 'no signal', never a
    warning (a pre-D4 substrate has no sidecars and is perfectly healthy)."""
    try:
        raw = json.loads(_rev_sidecar_path(Path(path)).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _try_restore_from_backup(path: Path) -> None:
    """Best-effort restore of `path` from the newest sibling backup under
    `_backups/`. Called by atomic_write_json_locked when post-write parse
    fails. Silent if no backup is found — the caller raises regardless, so
    the calling skill knows the write didn't take.
    """
    backups_dir = path.parent / "_backups"
    if not backups_dir.exists():
        return
    candidates = []
    for p in backups_dir.iterdir():
        if not p.is_file():
            continue
        # Match siblings of this file: entities.json* / aliases.json* etc.
        if p.name.startswith(path.stem):
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, p))
    if not candidates:
        return
    candidates.sort(reverse=True)
    newest = candidates[0][1]
    try:
        content = newest.read_text(encoding="utf-8")
        json.loads(content)  # only restore if backup itself parses
    except (OSError, json.JSONDecodeError):
        return
    try:
        atomic_write_text(path, content)
    except OSError:
        # Can't even restore. Caller will raise; user will need to manually
        # recover from the backup. Don't mask the original error.
        return


# Convenience: re-export common usage patterns to make caller code obvious.
__all__ = [
    "atomic_write_text",
    "atomic_write_json",
    "atomic_write_json_locked",
    "atomic_append_jsonl",
    "acquire_write_lock",
    "release_write_lock",
    "multi_write_context",
    "AtomicWriteLockError",
    "SubstrateRegressionError",
    "SeqHighWaterError",
    "AppendVerificationError",
    "check_substrate_regression",
    "events_freshness",
    "read_rev_sidecar",
]


# CLI mode for shell-based callers (e.g., bash skills that pipe JSON in).
def main() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Atomic write helper. Reads JSON or text from stdin, writes to --path atomically."
    )
    parser.add_argument("--path", required=True, type=Path, help="Destination path.")
    parser.add_argument(
        "--mode",
        choices=["text", "json", "append-jsonl"],
        default="text",
        help="Write mode. text = raw content. json = parse stdin as JSON, format with indent=2. "
        "append-jsonl = parse stdin as a JSON array of records, append each as a JSONL line.",
    )
    args = parser.parse_args()

    raw = sys.stdin.read()
    if args.mode == "text":
        atomic_write_text(args.path, raw)
    elif args.mode == "json":
        atomic_write_json(args.path, json.loads(raw))
    elif args.mode == "append-jsonl":
        events = json.loads(raw)
        if not isinstance(events, list):
            print("ERROR: append-jsonl mode requires a JSON array on stdin", file=sys.stderr)
            return 2
        atomic_append_jsonl(args.path, events)
    print(f"OK wrote {args.path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
