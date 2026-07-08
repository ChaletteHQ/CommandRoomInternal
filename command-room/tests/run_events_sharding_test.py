#!/usr/bin/env python3
"""SPEC A5 — events.jsonl yearly sharding. House conventions: check(name, cond) prints
OK/FAIL, exit 1 on any failure, auto-discovered by run_all.py."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import events_io as eio  # noqa: E402
import rotate_events as re_  # noqa: E402
import next_seq as ns  # noqa: E402
import cru_match as cm  # noqa: E402
import event_refs as er  # noqa: E402
import source_ref_index as sri  # noqa: E402

_failures: list[str] = []


def check(name: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="a5_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    return ws


def _write(ws: Path, name: str, events: list[dict]) -> None:
    (ws / "_hq" / "data" / name).write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")


def _active(ws: Path) -> Path:
    return ws / "_hq" / "data" / "events.jsonl"


def _ev(seq, year, **extra):
    d = {"seq": seq, "type": "interaction", "data": {"channel": "email"}}
    if year is not None:
        d["ts"] = f"{year}-06-01T00:00:00+00:00"
    d.update(extra)
    return d


def main() -> int:
    # ---- 1. shard_paths ordering ----
    ws = _ws()
    _write(ws, "events.jsonl", [_ev(1, 2026)])
    _write(ws, "events-2024.jsonl", [_ev(2, 2024)])
    _write(ws, "events-2025.jsonl", [_ev(3, 2025)])
    names = [p.name for p in eio.shard_paths(ws)]
    check("1: shard_paths chronological [2024, 2025, active]",
          names == ["events-2024.jsonl", "events-2025.jsonl", "events.jsonl"])
    ws2 = _ws(); _write(ws2, "events.jsonl", [_ev(1, 2026)])
    check("1: no-shard case -> [events.jsonl]",
          [p.name for p in eio.shard_paths(ws2)] == ["events.jsonl"])
    check("1: missing file -> []", eio.shard_paths(_ws()) == [])

    # ---- 2. iter_events concatenation + since_ts shard skip + defensive ----
    ws = _ws()
    _write(ws, "events-2024.jsonl", [_ev(1, 2024)])
    _write(ws, "events-2025.jsonl", [_ev(2, 2025)])
    (ws / "_hq" / "data" / "events.jsonl").write_text(
        json.dumps(_ev(3, 2026)) + "\ngarbage not json\n", encoding="utf-8")  # malformed in active
    allev = list(eio.iter_events(ws))
    check("2: iter_events concatenates all shards + active (defensive)",
          [e["seq"] for e in allev] == [1, 2, 3])
    since = [e["seq"] for e in eio.iter_events(ws, since_ts="2026-01-01")]
    check("2: since_ts=2026 skips 2024+2025 shards entirely", since == [3])

    # ---- 3 + 4. rotation correctness + seq continuity ----
    ws = _ws()
    events = [_ev(1, 2024), _ev(2, 2024), _ev(3, 2025), _ev(4, 2026), _ev(5, 2026),
              _ev(6, None)]  # undatable -> stays active
    _write(ws, "events.jsonl", events)
    res = re_.rotate(ws, now_iso="2026-07-01T00:00:00+00:00", force=True)
    check("3: rotation ran", res["rotated"] is True)
    s24 = [e["seq"] for e in eio._iter_file(ws / "_hq" / "data" / "events-2024.jsonl")]
    s25 = [e["seq"] for e in eio._iter_file(ws / "_hq" / "data" / "events-2025.jsonl")]
    check("3: 2024 shard has exactly its events", sorted(s24) == [1, 2])
    check("3: 2025 shard has exactly its events", sorted(s25) == [3])
    active = list(eio._iter_file(_active(ws)))
    active_types = [e.get("type") for e in active]
    check("3: active file opens with the shard_rotated marker", active_types[0] == "shard_rotated")
    active_seqs = sorted(e["seq"] for e in active if e.get("type") != "shard_rotated")
    check("3: active retains 2026 + undatable events", active_seqs == [4, 5, 6])
    # total event count conserved (originals across shards+active, excluding the marker)
    total_orig = len(s24) + len(s25) + len([e for e in active if e.get("type") != "shard_rotated"])
    check("3: total event count conserved", total_orig == 6)
    # 4. seq continuity — no reset, marker carries the high-water mark
    marker = next(e for e in active if e.get("type") == "shard_rotated")
    check("4: marker seq = max_overall + 1 (6 -> 7)", marker["seq"] == 7)
    check("4: marker carries max_archived_seq", marker["data"]["max_archived_seq"] == 3)
    nxt = ns.next_seq(str(_active(ws)))
    check("4: next_seq(active) > max_overall (no reset to 1)", nxt > 6)
    # a fresh append must not collide with any archived seq
    import atomic_write as aw
    aw.atomic_append_jsonl(_active(ws), [{"type": "note", "data": {}}])
    new_seqs = [e["seq"] for e in eio._iter_file(_active(ws)) if isinstance(e.get("seq"), int)]
    archived_all = set(s24) | set(s25)
    check("4: fresh append seq collides with no archived seq",
          not (set(new_seqs) & archived_all))

    # ---- 5. idempotency + dry-run ----
    res2 = re_.rotate(ws, now_iso="2026-07-01T00:00:00+00:00")  # no prior-year left in active
    check("5: second rotate is a no-op", res2["rotated"] is False)
    ws3 = _ws()
    _write(ws3, "events.jsonl", [_ev(1, 2024), _ev(2, 2026)])
    before = _active(ws3).read_bytes()
    re_.rotate(ws3, now_iso="2026-07-01T00:00:00+00:00", force=True, dry_run=True)
    check("5: --dry-run mutates nothing", _active(ws3).read_bytes() == before)

    # ---- 6. threshold gate: small prior-year file does NOT rotate ----
    ws4 = _ws()
    _write(ws4, "events.jsonl", [_ev(1, 2024), _ev(2, 2026)])  # tiny
    r = re_.rotate(ws4, now_iso="2026-07-01T00:00:00+00:00")  # default thresholds
    check("6: small file below threshold does not rotate", r["rotated"] is False)

    # ---- 7. back-compat: loaders are shard-transparent ----
    ws = _ws()
    _write(ws, "events-2025.jsonl", [_ev(1, 2025)])
    _write(ws, "events.jsonl", [_ev(2, 2026)])
    evs, _sk = cm.load_events_defensively(str(_active(ws)))
    check("7: load_events_defensively sees shards too", sorted(e["seq"] for e in evs) == [1, 2])
    check("7: event_refs.load_events sees shards too",
          sorted(e["seq"] for e in er.load_events(str(_active(ws)))) == [1, 2])
    # unsharded golden: identical to single-file read
    wsu = _ws(); _write(wsu, "events.jsonl", [_ev(1, 2026), _ev(2, 2026)])
    evs_u, _ = cm.load_events_defensively(str(_active(wsu)))
    check("7: unsharded workspace -> identical single-file output",
          [e["seq"] for e in evs_u] == [1, 2])

    # ---- 8. A3 interplay: index verifies post-rotation ----
    ws = _ws()
    _write(ws, "events.jsonl", [
        {"seq": 1, "ts": "2024-06-01T00:00:00+00:00", "type": "interaction",
         "data": {"channel": "email", "source_ref": "gmail:a"}},
        {"seq": 2, "ts": "2026-06-01T00:00:00+00:00", "type": "interaction",
         "data": {"channel": "email", "source_ref": "gmail:b"}},
    ])
    sri.rebuild(ws)
    re_.rotate(ws, now_iso="2026-07-01T00:00:00+00:00", force=True)
    # post-rotation the index should still verify against the ACTIVE file (its source of truth)
    check("8: source_ref_index.verify passes post-rotation", sri.verify(ws) is True)

    # ---- 9. integrity invariants ----
    ws = _ws()
    _write(ws, "events.jsonl", [_ev(1, 2024), _ev(2, 2026)])
    re_.rotate(ws, now_iso="2026-07-01T00:00:00+00:00", force=True)
    check("9: clean rotation -> no invariant violations", eio.shard_invariants(ws) == [])
    # cross-shard duplicate seq
    wsd = _ws()
    _write(wsd, "events-2024.jsonl", [_ev(7, 2024)])
    _write(wsd, "events.jsonl", [{"type": "shard_rotated", "seq": 99, "ts": "2026-01-01T00:00:00+00:00", "data": {}}, _ev(7, 2026)])
    check("9: cross-shard duplicate seq flagged",
          any("duplicated" in v for v in eio.shard_invariants(wsd)))
    # missing marker when shards exist
    wsm = _ws()
    _write(wsm, "events-2024.jsonl", [_ev(1, 2024)])
    _write(wsm, "events.jsonl", [_ev(2, 2026)])  # no marker
    check("9: missing marker flagged when shards exist",
          any("marker" in v for v in eio.shard_invariants(wsm)))

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} sharding check(s) failed:")
        for f in _failures:
            print(f"  - {f}")
        return 1
    print("ALL events-sharding checks PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
