#!/usr/bin/env python3
"""F-15 residue — dry-run timestamp normalizer test (v4.5.2 R4).

Fixture mirrors the REAL F-15 disk evidence (per the realdata-fixture gotcha):
the same day carried `commitment_resolved` in true UTC (18:49:05Z), a
commitment-triage pack_run stamped naive LOCAL (11:38:03 for an ~18:38 UTC
run), and an inbox pack_run at naive 14:00:00 (local 7:00 AM) — plus an
offset-aware auto-stamp shape, a bare date, a legacy `timestamp` spelling,
and a malformed line.

Asserts:
  - the tool classifies every shape correctly and proposes UTC conversions
    ONLY for the naive-local stamps (workspace tz, DST-correct);
  - the events file is BYTE-IDENTICAL after the run (report-only — the tool
    has no apply path at all);
  - the module exposes no apply/write flag (grep-level guard).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def _build_ws():
    root = tempfile.mkdtemp(prefix="cr-f15-")
    data_dir = os.path.join(root, "_hq", "data")
    os.makedirs(data_dir)
    events = [
        # True UTC (the good writer — F-16's closure).
        {"seq": 1, "ts": "2026-07-07T18:49:05Z", "type": "commitment_resolved",
         "source_skill": "commitment-triage",
         "data": {"commitment_id": "cmt_x", "resolution": "done"}},
        # Naive LOCAL (the F-15 triage pack_run: 11:38 PDT for an 18:38Z run).
        {"seq": 2, "ts": "2026-07-07T11:38:03", "type": "pack_run",
         "source_skill": "commitment-triage", "data": {"kind": "commitment-triage"}},
        # Naive LOCAL (the F-15 inbox pack_run: 14:00 for local 7:00 AM —
        # actually naive-local 07:00? disk said 14:00:00 local-stamped).
        {"seq": 3, "ts": "2026-07-08T14:00:00", "type": "pack_run",
         "source_skill": "inbox", "data": {"kind": "inbox"}},
        # Offset-aware (the auto-stamp +00:00 shape) — NOT flagged.
        {"seq": 4, "ts": "2026-07-08T13:09:00+00:00", "type": "sent_reconcile",
         "source_skill": "reconcile-sent", "data": {"n_closed": 0}},
        # Bare date — carries no clock; NOT a naive-clock proposal.
        {"seq": 5, "ts": "2026-07-01", "type": "note",
         "source_skill": "session-sweep", "data": {}},
        # Legacy `timestamp` field spelling, aware — counted as legacy, not naive.
        {"seq": 6, "timestamp": "2026-06-30T10:00:00Z", "type": "interaction",
         "source_skill": "historical-backfill", "data": {}},
    ]
    path = os.path.join(data_dir, "events.jsonl")
    with open(path, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        f.write("{ not json\n")  # malformed line — skipped, reported, untouched
    # Workspace tz — America/Los_Angeles, the real workspace value.
    with open(os.path.join(data_dir, "entities.json"), "w", encoding="utf-8") as f:
        json.dump({"workspace": {"user_timezone": "America/Los_Angeles"}}, f)
    return root, path


def main():
    from normalize_timestamps_dryrun import run_report

    root, events_path = _build_ws()
    before = open(events_path, "rb").read()

    print("\n[1] classification + proposals over the real F-15 shapes")
    out = run_report(root, limit=10)
    c = out["counts"]
    check("aware UTC counted (Z + +00:00)", c["aware_utc"] == 3, c)
    check("exactly the two naive-local stamps flagged", c["naive"] == 2, c)
    check("bare date not flagged as naive", c["bare_date"] == 1, c)
    check("legacy `timestamp` spelling surfaced",
          out["legacy_fields"]["timestamp"] == 1, out["legacy_fields"])

    props = {p[2]: p for p in out["proposals"]}  # by seq
    check("both proposals are the naive events (seq 2 + 3)",
          set(props) == {2, 3}, sorted(props))
    # 11:38:03 PDT (UTC-7 in July) -> 18:38:03Z: the F-15 anomaly resolved.
    check("triage pack_run normalizes 11:38:03 local -> 18:38:03 UTC",
          props.get(2, ("",) * 6)[5] == "2026-07-07T18:38:03+00:00",
          props.get(2))
    check("inbox pack_run normalizes 14:00 local -> 21:00 UTC",
          props.get(3, ("",) * 6)[5] == "2026-07-08T21:00:00+00:00",
          props.get(3))

    print("\n[2] report-only: substrate byte-identical, no apply path exists")
    after = open(events_path, "rb").read()
    check("events.jsonl byte-identical after the run", before == after)
    src = open(os.path.join(PLUGIN_ROOT, "shared", "scripts",
                            "normalize_timestamps_dryrun.py"),
               encoding="utf-8").read()
    check("tool has no --apply flag and never opens events for writing",
          'add_argument("--apply"' not in src and '"w"' not in src
          and "atomic_append" not in src and "atomic_write" not in src, "")

    shutil.rmtree(root, ignore_errors=True)
    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
