#!/usr/bin/env python3
"""HYG1 Item 3 — retire the remaining `last_activity` fossil readers (the
C3/F-54 follow-through). Pins:

1. Resolver recency tiebreak: the fossil says thread A is newest but EVENTS
   say thread B → `resolve_to_linked_project` picks B (person walk AND org
   walk).
2. Zero-event floor survives: threads with NO event history fall back to
   the stored stamp, then first_seen (the DATA_CONTRACT carve-out).
3. Lazy-derivation perf guard: a single-candidate walk never scans events
   (`_sort_by_observed_recency` returns immediately at len <= 1).
4. Workspace-map / DCC last-touched displays prefer derived activity over
   the record stamp, with the stamp as the zero-event floor.

Fixture dates are computed RELATIVE TO TODAY (hardcoded-future-date gotcha).
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import entity_resolve as er  # noqa: E402
from entity_resolve import resolve_to_linked_project  # noqa: E402

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


TODAY = dt.date.today()


def d(days_ago):
    return (TODAY - dt.timedelta(days=days_ago)).isoformat()


def iso(days_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def _ws(threads, events):
    ws = Path(tempfile.mkdtemp())
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "entities": {
            "people": [{"id": "person_001", "canonical_name": "Avery Sample",
                        "primary_org_id": "org_001"}],
            "orgs": [{"id": "org_001", "canonical_name": "Acme Co"}],
            "threads": threads,
        }
    }), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return ws


def thread(tid, name, *, last_activity=None, first_seen=None):
    t = {"id": tid, "canonical_name": name, "status": "active",
         "affiliation_id": "org_001", "key_contact_id": "person_001"}
    if last_activity:
        t["last_activity"] = last_activity
    if first_seen:
        t["first_seen"] = first_seen
    return t


def meeting(seq, tid, days_ago):
    return {"seq": seq, "ts": iso(days_ago), "type": "meeting",
            "source_skill": "test", "primary_thread_id": tid,
            "data": {"title": "sync"}}


def main():
    print("=== HYG1 Item 3 — fossil-reader retirement ===\n")

    # --- 1. events beat the fossil in the resolver tiebreak -------------------
    # Fossil claims A was touched 2 days ago; events show B met yesterday and
    # A's real activity is 40 days old. Pre-HYG1 both walks picked A.
    threads = [
        thread("project_001", "Alpha engagement", last_activity=d(2)),
        thread("project_002", "Beta engagement", last_activity=d(60)),
    ]
    events = [meeting(1, "project_001", 40), meeting(2, "project_002", 1)]
    ws = _ws(threads, events)

    r = resolve_to_linked_project(ws, "Avery Sample")
    check("person walk: events beat the fossil (B wins)",
          r is not None and r.record.get("id") == "project_002",
          r and r.record.get("id"))
    r = resolve_to_linked_project(ws, "Acme Co")
    check("org walk: events beat the fossil (B wins)",
          r is not None and r.record.get("id") == "project_002",
          r and r.record.get("id"))

    # --- 2. zero-event floor: stamp, then first_seen ---------------------------
    threads = [
        thread("project_001", "Alpha engagement", last_activity=d(3)),
        thread("project_002", "Beta engagement", last_activity=d(30)),
    ]
    ws = _ws(threads, [])  # no events at all
    r = resolve_to_linked_project(ws, "Acme Co")
    check("zero-event threads: the record stamp is still the floor (A wins)",
          r is not None and r.record.get("id") == "project_001",
          r and r.record.get("id"))

    threads = [
        thread("project_001", "Alpha engagement", first_seen=d(50)),
        thread("project_002", "Beta engagement", first_seen=d(5)),
    ]
    ws = _ws(threads, [])
    r = resolve_to_linked_project(ws, "Acme Co")
    check("no stamp either: first_seen fallback (newest first_seen wins)",
          r is not None and r.record.get("id") == "project_002",
          r and r.record.get("id"))

    # --- 3. perf guard: single candidate never derives --------------------------
    calls = {"n": 0}
    real_sort = er._sort_by_observed_recency

    def _counting_derive(*a, **k):
        calls["n"] += 1
        return {}

    import thread_activity as ta
    orig = ta.derive_thread_activity
    ta.derive_thread_activity = _counting_derive
    try:
        one = [{"id": "project_001"}]
        out = real_sort(one, ws)
        check("single-candidate list returns without deriving",
              out == one and calls["n"] == 0, calls)
        real_sort([{"id": "project_001"}, {"id": "project_002"}], ws)
        check("multi-candidate list derives exactly once", calls["n"] == 1, calls)
    finally:
        ta.derive_thread_activity = orig

    # --- 4. display builders prefer derived activity ----------------------------
    from build_workspace_map_input import _project_last_when
    act_ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)
    act = {"project_001": SimpleNamespace(ts=act_ts)}
    p_fossil = {"id": "project_001", "last_activity": d(30)}
    derived = _project_last_when(p_fossil, act)
    check("workspace-map last-touched prefers derived activity",
          derived == act_ts.isoformat(), derived)
    check("workspace-map zero-event floor: stamp still read",
          _project_last_when({"id": "project_009", "last_activity": d(30)}, act)
          == d(30))
    check("workspace-map legacy no-activity-map call unchanged",
          _project_last_when({"id": "project_009", "last_touched": d(7)}) == d(7))

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
