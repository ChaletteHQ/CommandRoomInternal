#!/usr/bin/env python3
"""SPEC A3 — source_ref dedup index tests (incl. the 250-event window-bug repro)."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import source_ref_index as sri  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _ws(events=None):
    ws = Path(tempfile.mkdtemp(prefix="a3_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    if events is not None:
        (ws / "_hq" / "data" / "events.jsonl").write_text(
            "\n".join(json.dumps(e) if isinstance(e, dict) else e for e in events) + "\n",
            encoding="utf-8")
    return ws


def test_keys_of_shapes():
    check("data.dedup_hash shape", sri._keys_of({"data": {"dedup_hash": "abc123"}}) == {"h:abc123"})
    check("top-level source_ref_hash shape", sri._keys_of({"source_ref_hash": "def456"}) == {"h:def456"})
    r = sri._keys_of({"data": {"source_ref": "gmail:msg1"}})
    check("data.source_ref shape -> r: key", len(r) == 1 and next(iter(r)).startswith("r:"))
    check("no keys / shapeless -> empty set, no raise", sri._keys_of({}) == set() and sri._keys_of("x") == set())


def test_rebuild_mixed_and_malformed():
    events = []
    for i in range(500):
        events.append({"data": {"dedup_hash": f"h{i:04d}", "source_ref": f"src:{i}"}})
    events.append("this is a corrupt line")
    events.append({"no_keys": True})
    ws = _ws(events)
    stats = sri.rebuild(ws)
    # 500 events x (1 h-key + 1 r-key) = 1000 keys; malformed + no-key skipped.
    check("rebuild key count = 1000", stats["keys"] == 1000)
    check("rebuild counted 501 dict events (incl no_keys)", stats["events"] == 501)
    check("idx contains a known h-key", sri.check(ws, dedup_hash="h0001"))
    check("idx contains a known r-key (source_ref)", sri.check(ws, source_ref="src:1"))


def test_check_hit_miss_and_normalization():
    ws = _ws([{"data": {"dedup_hash": "hh", "source_ref": "Gmail:ABC"}}])
    sri.rebuild(ws)
    check("h-key hit", sri.check(ws, dedup_hash="hh"))
    check("h-key miss", not sri.check(ws, dedup_hash="nope"))
    check("r-key normalization round-trip (case/space)", sri.check(ws, source_ref="  gmail:abc  "))
    check("r-key miss", not sri.check(ws, source_ref="other:zzz"))


def test_window_bug_reproducer():
    # 250 events; event #0 has source_ref "OLD". The old last-200 scan can't see it.
    events = [{"data": {"source_ref": "OLD", "summary": "first"}}]
    for i in range(1, 250):
        events.append({"data": {"source_ref": f"s{i}", "summary": str(i)}})
    ws = _ws(events)
    sri.rebuild(ws)
    # Naive last-200 scan: event #0 is at index 0, outside the last 200 -> miss.
    last200 = events[-200:]
    naive_hit = any((e.get("data") or {}).get("source_ref") == "OLD" for e in last200)
    check("last-200 scan MISSES the 250-old duplicate (documents the bug)", naive_hit is False)
    check("index CATCHES the 250-old duplicate", sri.check(ws, source_ref="OLD"))


def test_lazy_migration():
    ws = _ws([{"data": {"source_ref": "lazy:1"}}])  # events.jsonl present, no .idx
    check("idx absent before first check", not (ws / "_hq" / "data" / ".source_refs.idx").exists())
    check("first check builds the idx and answers", sri.check(ws, source_ref="lazy:1"))
    check("idx now exists", (ws / "_hq" / "data" / ".source_refs.idx").exists())


def test_atomic_append_integration():
    from atomic_write import atomic_append_jsonl
    ws = _ws([])
    ep = ws / "_hq" / "data" / "events.jsonl"
    atomic_append_jsonl(ep, [
        {"type": "interaction", "data": {"source_ref": "intg:1", "dedup_hash": "ih1"}},
        {"type": "interaction", "data": {"source_ref": "intg:2"}},
        {"type": "interaction", "data": {}},  # no keys
    ])
    idx = (ws / "_hq" / "data" / ".source_refs.idx").read_text(encoding="utf-8")
    check("append maintained idx with h-key", "h:ih1" in idx)
    check("append maintained idx with r-keys", sri.check(ws, source_ref="intg:1") and sri.check(ws, source_ref="intg:2"))
    # Index failure must not fail an event write: record_keys swallows everything.
    raised = False
    try:
        sri.record_keys("/nonexistent/zzz", [{"data": {"source_ref": "x"}}])
    except Exception:
        raised = True
    check("record_keys never raises on a bad root", raised is False)
    check("event write itself succeeded (3 events on disk)", len([l for l in ep.read_text(encoding='utf-8').splitlines() if l.strip()]) == 3)


def test_verify_and_idempotent_rebuild():
    ws = _ws([{"data": {"source_ref": "v:1"}}, {"data": {"source_ref": "v:2"}}])
    sri.rebuild(ws)
    check("verify True on a clean index", sri.verify(ws))
    # Corrupt: append a bogus key + drop a real one.
    idxp = ws / "_hq" / "data" / ".source_refs.idx"
    idxp.write_text("r:deadbeefdeadbeef\n", encoding="utf-8")
    check("verify detects corruption", not sri.verify(ws))
    sri.rebuild(ws)
    check("rebuild repairs -> verify True", sri.verify(ws))
    first = idxp.read_text(encoding="utf-8")
    sri.rebuild(ws)
    check("rebuild is idempotent (byte-identical)", idxp.read_text(encoding="utf-8") == first)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_keys_of_shapes()
    test_rebuild_mixed_and_malformed()
    test_check_hit_miss_and_normalization()
    test_window_bug_reproducer()
    test_lazy_migration()
    test_atomic_append_integration()
    test_verify_and_idempotent_rebuild()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL source_ref_index tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
