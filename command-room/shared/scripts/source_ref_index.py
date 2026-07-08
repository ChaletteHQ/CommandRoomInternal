#!/usr/bin/env python3
"""
source_ref dedup index (SPEC A3) — O(1) membership set replacing "scan the last
200 events for a matching hash".

An active workspace emits 200 events in well under a week, so the last-200 scan
silently re-captures any duplicate older than the window (re-processing a
month-old transcript or a Drive backfill re-captured everything). This sidecar
index at `_hq/data/.source_refs.idx` is a plain-text membership set — one key
per line, two namespaces — that is O(1) to check, append-maintained inside the
A1 writer lock by `atomic_append_jsonl`, rebuildable from events.jsonl, and
verified weekly by cleanup.

Key namespaces (absorbs the documented hash drift — skills disagree on which
value they compute, so we index both):
  - `h:<dedup_hash>`  the PASSIVE_CAPTURE 12-hex hash (`data.dedup_hash` or the
                      top-level `source_ref_hash` in the WORKSPACE_API shape)
  - `r:<sha256(normalized source_ref)[:16]>`  the raw `data.source_ref` string

The index is a CACHE over events.jsonl (the source of truth); corruption
self-heals via `rebuild`. Index writes must NEVER fail an event append.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Iterable, Optional, Set

_IDX_NAME = ".source_refs.idx"


def _data_dir(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data"


def _idx_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / _IDX_NAME


def _events_path(workspace_root) -> Path:
    return _data_dir(workspace_root) / "events.jsonl"


def _norm_source_ref(s: str) -> str:
    return (s or "").strip().lower()


def _r_key(source_ref: str) -> str:
    return "r:" + hashlib.sha256(_norm_source_ref(source_ref).encode("utf-8")).hexdigest()[:16]


def _keys_of(event: dict) -> Set[str]:
    """Extract the `h:`/`r:` keys from an event across all observed shapes.
    Defensive — never raises; returns an empty set for a shapeless event."""
    keys: Set[str] = set()
    if not isinstance(event, dict):
        return keys
    data = event.get("data") if isinstance(event.get("data"), dict) else {}
    dedup_hash = (
        data.get("dedup_hash")
        or event.get("dedup_hash")
        or event.get("source_ref_hash")
        or data.get("source_ref_hash")
    )
    if dedup_hash:
        keys.add("h:" + str(dedup_hash))
    source_ref = data.get("source_ref") or event.get("source_ref")
    if isinstance(source_ref, str) and source_ref.strip():
        keys.add(_r_key(source_ref))
    return keys


def _load_idx(workspace_root) -> Set[str]:
    p = _idx_path(workspace_root)
    if not p.exists():
        return set()
    out: Set[str] = set()
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            out.add(line)
    return out


def _derive_keys(workspace_root) -> tuple[Set[str], int]:
    """Re-derive the full key set from events.jsonl. Defensive line-by-line."""
    ep = _events_path(workspace_root)
    keys: Set[str] = set()
    n = 0
    if ep.exists():
        for line in ep.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            n += 1
            keys |= _keys_of(ev)
    return keys, n


def rebuild(workspace_root) -> dict:
    """Rebuild the index from events.jsonl. Sorted + deduped → idempotent
    (byte-identical on a re-run)."""
    keys, n = _derive_keys(workspace_root)
    p = _idx_path(workspace_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(sorted(keys)) + ("\n" if keys else ""), encoding="utf-8")
    return {"events": n, "keys": len(keys)}


def record_keys(workspace_root, events: Iterable[dict]) -> int:
    """Append any NEW keys from `events` to the index. Best-effort — called from
    inside `atomic_append_jsonl`'s writer-lock scope; never raises. Returns the
    count of keys added."""
    try:
        existing = _load_idx(workspace_root)
        new: Set[str] = set()
        for ev in events:
            new |= _keys_of(ev)
        add = new - existing
        if not add:
            return 0
        p = _idx_path(workspace_root)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            for k in sorted(add):
                f.write(k + "\n")
        return len(add)
    except Exception:
        return 0


def check(workspace_root, source_ref: Optional[str] = None,
          dedup_hash: Optional[str] = None) -> bool:
    """True if this source_ref / dedup_hash is already captured. Read-only and
    lock-free (a stale read at worst lets one duplicate through — same as the old
    race; capture must never block). Lazy-migrates: builds the index from
    events.jsonl on first check when the idx is missing."""
    if not _idx_path(workspace_root).exists() and _events_path(workspace_root).exists():
        rebuild(workspace_root)
    idx = _load_idx(workspace_root)
    if dedup_hash and ("h:" + str(dedup_hash)) in idx:
        return True
    if source_ref and _r_key(source_ref) in idx:
        return True
    return False


def verify(workspace_root) -> bool:
    """True iff the index exactly matches the keys derived from events.jsonl.
    cleanup rebuilds on mismatch (the index is a cache; events.jsonl is truth)."""
    derived, _ = _derive_keys(workspace_root)
    return derived == _load_idx(workspace_root)


__all__ = ["check", "rebuild", "verify", "record_keys", "_keys_of"]


def _main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="source_ref_index.py")
    ap.add_argument("cmd", choices=["check", "rebuild", "verify"])
    ap.add_argument("workspace_root")
    ap.add_argument("--source-ref")
    ap.add_argument("--dedup-hash")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        hit = check(a.workspace_root, source_ref=a.source_ref, dedup_hash=a.dedup_hash)
        print("HIT" if hit else "MISS")
        return 0 if hit else 1
    if a.cmd == "rebuild":
        print(json.dumps(rebuild(a.workspace_root)))
        return 0
    ok = verify(a.workspace_root)
    print("OK" if ok else "MISMATCH")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(_main(sys.argv[1:]))
