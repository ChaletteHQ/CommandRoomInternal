"""The owner-confirmed repair of a store file that cannot be read (cr1#1847,
decision 147t).

One reviewed pattern for "an unreadable file is set aside only on the
owner's yes". Its first caller is the Prep page's edits file
(`hub_prep_edit`). It is shaped so the off-limits list (`mail_rules`,
cr1#1831) can move onto it once #1831 has passed UAT; that move is a
follow-up, not part of this change. Nothing here names a store: each call
takes the file's path and its group name (the file's own name, as the
backups folder and the folder tidy group it).

The parts:

  * KEPT COPY (`keep_copy`). After each clean write, the store just written
    is kept in `_hq/data/_backups/<group>.<YYYYMMDD>.backup`, one slot a
    day; a later clean write that day replaces it with the newer good copy.
    The folder tidy keeps the three newest.
  * SIGHTING (`note_sighting`). Each Maintenance fire notes what it saw. A
    file that reads, or no file, clears the note. An unreadable file is
    noted by its fingerprint (size, mtime_ns, sha256 of its bytes). A
    sighting holds for one fingerprint only: any other fingerprint starts a
    new note from scratch. An EPISODE is confirmed when one fingerprint has
    stayed unreadable across `EPISODE_MIN_FIRES` fires at least
    `EPISODE_MIN_GAP_MINUTES` apart, first to last, so a file that is only
    mid-sync reads again, or changes, before anything is offered.
  * OFFER (`offer`). Only for a confirmed episode whose fingerprint is still
    the file's: the last readable copy's date and size (or "start clean"
    when there is none), a conflict copy beside the file named but never
    read, and a token over the fingerprint and the copy.
  * CONFIRM (`confirm`). Under the store's write lock, only with the token
    the offer returned. The file is read again: one that now reads, one
    whose fingerprint changed, or a copy other than the one shown refuses
    and writes nothing. Then the damaged bytes go into a fresh dated
    `_archive/cleanup-*` folder with a MANIFEST.md, and the live file is
    replaced, atomically, by the copy (or an empty store). The live file is
    never absent and nothing is deleted.

Never raises out of `note_sighting`, `offer` or `episode`."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

#: Decision 147t (1): an episode is one fingerprint seen unreadable on at least
#: this many Maintenance fires ...
EPISODE_MIN_FIRES = 2
#: ... at least this many minutes apart, first to last (a sync stall that
#: clears inside the hour never reaches the owner).
EPISODE_MIN_GAP_MINUTES = 60

BACKUP_DIR_REL = Path("_hq") / "data" / "_backups"
BACKUP_SUFFIX = ".backup"
SIGHTING_SUFFIX = ".sighting.json"
ARCHIVE_ROOT = "_archive"
ARCHIVE_PREFIX = "cleanup-"
#: HQRESOLVE1: an archive never holds a folder named `_hq`.
HQ_MIRROR = "hq-system-locks"


def _now(now_iso: Optional[str] = None) -> _dt.datetime:
    if now_iso:
        s = str(now_iso)
        t = _dt.datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith(("Z", "z")) else s)
        return t.astimezone(_dt.timezone.utc) if t.tzinfo else t.replace(tzinfo=_dt.timezone.utc)
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _iso(d: _dt.datetime) -> str:
    return d.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _parse(value: Any) -> Optional[_dt.datetime]:
    try:
        return _now(str(value)) if value else None
    except (TypeError, ValueError):
        return None


def fingerprint(path: Path) -> Optional[Dict[str, Any]]:
    """What a file is, by its bytes and its stamp: `{size, mtime_ns, sha256}`;
    None when it is not there or cannot be read as bytes."""
    try:
        st = path.stat()
        raw = path.read_bytes()
    except OSError:
        return None
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns), "sha256": hashlib.sha256(raw).hexdigest()}


# ---------------------------------------------------------------- the kept copy
def backups_dir(root: Path) -> Path:
    return Path(root) / BACKUP_DIR_REL


def keep_copy(root: Path, group: str, store: Any, now_iso: Optional[str] = None) -> None:
    """A store that was just written and read back clean, into today's slot.
    Best-effort: a copy that cannot be written never stops the owner's own
    write."""
    from atomic_write import atomic_write_json

    now = _now(now_iso)
    dest = backups_dir(root) / f"{group}.{now:%Y%m%d}{BACKUP_SUFFIX}"
    try:
        atomic_write_json(dest, {"version": 1, "taken_at": _iso(now), "store": store})
    except Exception:  # noqa: BLE001
        pass


def copies(root: Path, group: str, reads: Callable[[Any], bool]) -> List[Dict[str, Any]]:
    """The kept copies whose store `reads`, newest first, each `{path,
    taken_at, store}`. A copy that does not read is skipped, never offered."""
    out: List[Dict[str, Any]] = []
    d = backups_dir(root)
    if not d.is_dir():
        return out
    for p in d.iterdir():
        if not (p.is_file() and p.name.startswith(group + ".") and p.name.endswith(BACKUP_SUFFIX)):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
            at = _parse(doc.get("taken_at"))
            if at is None or not reads(doc.get("store")):
                continue
        except Exception:  # noqa: BLE001 - an unreadable copy is no copy
            continue
        out.append({"path": p, "taken_at": at, "store": doc.get("store")})
    out.sort(key=lambda c: c["taken_at"], reverse=True)
    return out


# ---------------------------------------------------------------- the sighting
def sighting_path(path: Path) -> Path:
    return path.with_name(path.name + SIGHTING_SUFFIX)


def _read_sighting(path: Path) -> Optional[Dict[str, Any]]:
    try:
        doc = json.loads(sighting_path(path).read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) and isinstance(doc.get("fingerprint"), dict) else None
    except Exception:  # noqa: BLE001
        return None


def _clear_sighting(path: Path) -> None:
    """The note cleared by OVERWRITING it, never by deleting it: the owner's
    mount refuses deletes (rule 140h; review of #1847 at 01cad81e1, must 1
    and 2). A note with no fingerprint reads as no note (`_read_sighting`).
    Nothing is written when there is no note, or it is already cleared."""
    from atomic_write import atomic_write_json

    sp = sighting_path(path)
    if not sp.exists():
        return
    try:
        if json.loads(sp.read_text(encoding="utf-8")) == CLEARED:
            return
    except Exception:  # noqa: BLE001 - a damaged note is overwritten too
        pass
    atomic_write_json(sp, CLEARED)


#: A cleared sighting note: no fingerprint, so it is no note.
CLEARED = {"version": 1, "cleared": True}


class BytesChanged(Exception):
    """The file's bytes are not the ones the offer was made for (a sync
    landed between the check and the archive)."""


def _confirmed(s: Optional[Dict[str, Any]]) -> bool:
    if not s:
        return False
    first, last = _parse(s.get("first_seen")), _parse(s.get("last_seen"))
    fires = s.get("fires")
    return (isinstance(fires, int) and fires >= EPISODE_MIN_FIRES and first is not None and last is not None
            and last - first >= _dt.timedelta(minutes=EPISODE_MIN_GAP_MINUTES))


def note_sighting(path: Path, readable: Callable[[Path], bool], now_iso: Optional[str] = None) -> Dict[str, Any]:
    """A WRITER (the sighting note only; the store is never touched). One
    Maintenance fire's look at the file. Outcomes: `clear` (no file, or it
    reads: any note is removed), `noted` (a new or continued sighting, not yet
    an episode), `episode` (confirmed), `error`. Never raises."""
    from atomic_write import atomic_write_json

    try:
        if not path.exists() or readable(path):
            _clear_sighting(path)
            return {"outcome": "clear"}
        fp = fingerprint(path)
        if fp is None:
            _clear_sighting(path)
            return {"outcome": "clear"}
        now = _iso(_now(now_iso))
        s = _read_sighting(path)
        if s is None or s.get("fingerprint") != fp:
            # a new fingerprint is a new sighting: nothing carries over
            s = {"version": 1, "fingerprint": fp, "first_seen": now, "last_seen": now, "fires": 1}
        else:
            s = dict(s, last_seen=now, fires=int(s.get("fires") or 0) + 1)
        atomic_write_json(sighting_path(path), s)
        return {"outcome": "episode" if _confirmed(s) else "noted", "sighting": s}
    except Exception as exc:  # noqa: BLE001
        return {"outcome": "error", "reason": type(exc).__name__}


def episode(path: Path) -> Optional[Dict[str, Any]]:
    """The confirmed episode the file is in now, or None: the note must be
    confirmed AND name the file's fingerprint as it stands (a note about
    other bytes confirms nothing). Read only."""
    s = _read_sighting(path)
    if not _confirmed(s):
        return None
    return s if fingerprint(path) == s.get("fingerprint") else None


# ---------------------------------------------------------------- the offer
def conflict_copies(path: Path) -> List[str]:
    """A sync service's conflict copies beside the file (`<stem>-<anything>
    <suffix>`, as OneDrive names them), by name only. Never read."""
    try:
        return sorted(p.name for p in path.parent.glob(f"{path.stem}-*{path.suffix}")
                      if p.is_file() and p.name != path.name)
    except OSError:
        return []


def token(fp: Dict[str, Any], taken_at: Optional[str], first_seen: Optional[str] = None) -> str:
    """The offer's token: the damaged bytes, the copy offered, and the episode
    it belongs to (review at 01cad81e1, should 4: an old token never repairs a
    new episode of the same bytes)."""
    raw = json.dumps({"fp": fp, "copy": taken_at or "", "since": first_seen or ""}, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def offer(root: Path, path: Path, group: str, reads: Callable[[Any], bool]) -> Dict[str, Any]:
    """READ. What a yes would do, for a confirmed episode only. Outcomes:
    `none` (no confirmed episode: nothing is offered), `offer` with
    `{token, copy_taken_at (or None: start clean), copy_store, conflicts}`."""
    s = episode(path)
    if s is None:
        return {"outcome": "none"}
    top = (copies(root, group, reads) or [None])[0]
    taken = _iso(top["taken_at"]) if top else None
    return {"outcome": "offer", "token": token(s["fingerprint"], taken, s.get("first_seen")), "copy_taken_at": taken,
            "copy_store": top["store"] if top else None, "conflicts": conflict_copies(path),
            "since": s.get("first_seen"), "fingerprint": s["fingerprint"]}


# ---------------------------------------------------------------- the yes
def claim_archive(root: Path, day: str, label: str) -> str:
    """A fresh dated archive folder, never one that exists (the folder tidy's
    own rule: an existing dated folder is never written into)."""
    arch = Path(root) / ARCHIVE_ROOT
    arch.mkdir(parents=True, exist_ok=True)
    base = f"{ARCHIVE_PREFIX}{day}-{label}"
    name, n = base, 2
    while True:
        try:
            (arch / name).mkdir()
            return name
        except FileExistsError:
            name = f"{base}-{n}"
            n += 1


def archive_aside(root: Path, path: Path, rel: Path, label: str, day: str, manifest: str,
                  expect_sha256: Optional[str] = None) -> str:
    """The file's bytes, as found, into a fresh dated archive folder beside a
    MANIFEST.md; the live file is not touched. Answers the archived path,
    relative to the workspace. With `expect_sha256`, bytes that are not the
    ones checked raise `BytesChanged` before anything is written (review at
    01cad81e1, should 3: a sync that lands mid-repair is never archived as
    the damage and then overwritten)."""
    from atomic_write import atomic_write_text

    raw = path.read_bytes()
    if expect_sha256 is not None and hashlib.sha256(raw).hexdigest() != expect_sha256:
        raise BytesChanged()
    bucket = claim_archive(root, day, label)
    mirror = Path(*(HQ_MIRROR if part == "_hq" else part for part in rel.parts))
    dest = Path(root) / ARCHIVE_ROOT / bucket / mirror
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(raw)
    atomic_write_text(Path(root) / ARCHIVE_ROOT / bucket / "MANIFEST.md", manifest.format(rel=mirror.as_posix()))
    return (Path(ARCHIVE_ROOT) / bucket / mirror).as_posix()


def confirm(root: Path, path: Path, group: str, reads: Callable[[Any], bool], readable: Callable[[Path], bool],
            given: Optional[str], *, empty: Callable[[], Any], rel: Path, label: str, day: str,
            manifest: Callable[[Optional[str], List[str], str], str]) -> Dict[str, Any]:
    """A WRITER, to be called under the store's own write lock. The owner's
    yes, tied to the offer shown. Outcomes: `ask_first` (no token handed
    back), `not_needed` (the file reads now, or is gone), `changed` (the
    file's bytes or the copy are not what was shown: a fresh offer rides
    along), `none` (no confirmed episode), `repaired` with `{archived_to,
    copy_taken_at}`, `failed` (archived, but the replace was refused: the
    live file is as it was, and the MANIFEST says so). Nothing is written on
    the first four. `manifest(copy_at, conflicts, state)` words the MANIFEST
    for `pending` (written with the archive), then `done` or `failed`
    (written over it once the replace has answered)."""
    from atomic_write import atomic_write_json, atomic_write_text

    if not path.exists() or readable(path):
        _clear_sighting(path)
        return {"outcome": "not_needed"}
    now_offer = offer(root, path, group, reads)
    if now_offer["outcome"] != "offer":
        return {"outcome": "none"}
    if not given:
        return {"outcome": "ask_first", "offer": now_offer}
    if given != now_offer["token"]:
        return {"outcome": "changed", "offer": now_offer}
    copy_at, conflicts = now_offer["copy_taken_at"], now_offer["conflicts"]
    try:
        archived = archive_aside(root, path, rel, label, day, manifest(copy_at, conflicts, "pending"),
                                 expect_sha256=now_offer["fingerprint"]["sha256"])
    except BytesChanged:
        return {"outcome": "changed", "offer": offer(root, path, group, reads)}
    mf = Path(root) / Path(archived).parents[len(rel.parts) - 1] / "MANIFEST.md"
    try:
        atomic_write_json(path, now_offer["copy_store"] if copy_at else empty())
    except Exception as exc:  # noqa: BLE001 - the live file is as it was; the record says so
        try:
            atomic_write_text(mf, manifest(copy_at, conflicts, "failed").format(rel=Path(archived).relative_to(
                Path(archived).parents[len(rel.parts) - 1]).as_posix()))
        except Exception:  # noqa: BLE001
            pass
        return {"outcome": "failed", "archived_to": archived, "reason": type(exc).__name__}
    try:
        atomic_write_text(mf, manifest(copy_at, conflicts, "done").format(rel=Path(archived).relative_to(
            Path(archived).parents[len(rel.parts) - 1]).as_posix()))
    except Exception:  # noqa: BLE001 - the repair stands; the MANIFEST keeps its pending words
        pass
    try:
        _clear_sighting(path)
    except Exception:  # noqa: BLE001 - the repair stands; the file reads now, which clears the note next fire
        pass
    return {"outcome": "repaired", "archived_to": archived, "copy_taken_at": copy_at}


__all__ = ["EPISODE_MIN_FIRES", "EPISODE_MIN_GAP_MINUTES", "fingerprint", "keep_copy", "copies", "note_sighting",
           "episode", "conflict_copies", "offer", "confirm", "archive_aside", "claim_archive", "sighting_path"]
