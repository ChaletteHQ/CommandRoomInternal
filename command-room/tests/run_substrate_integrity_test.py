#!/usr/bin/env python3
"""Substrate integrity (T2 — FS-01/03/04/05/06/07/15).

  FS-03: org/people/engagement writers stamp UTC-aware time and OMIT ts on
         event appends (the append gate stamps UTC) — no naive-local drift.
  FS-04: the events.jsonl seq high-water guard refuses an in-place append when
         the on-disk log regressed below the recorded high-water, quarantines
         the batch, drops a loud marker, and raises.
  FS-05/15: core substrate JSON that won't parse is surfaced LOUDLY, never
         swallowed.
  FS-06: duplicate seq numbers are counted for the health check.
  FS-07: the schedule registration add-flow writes schedule_created.
  FS-01: the update bridge runs Phase 4.7 state-gated blocks before the
         up-to-date early-exit.

House convention: non-zero exit = fail.
"""

from __future__ import annotations

import os
import re
import sys
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label + (f" — {detail}" if detail else ""))
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))


def _mk_ws() -> str:
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "_hq", "data"))
    return d


def main() -> int:
    # ---- FS-03: UTC-aware _now_iso + no hand-stamped ts on event appends ---
    import org_writer, people_writer, engagement_writer
    for mod in (org_writer, people_writer, engagement_writer):
        iso = mod._now_iso()
        check(f"{mod.__name__}._now_iso is UTC-aware",
              iso.endswith("+00:00") or iso.endswith("Z"), iso)
    for name in ("org_writer.py", "people_writer.py", "engagement_writer.py"):
        src = (ROOT / "shared" / "scripts" / name).read_text(encoding="utf-8")
        check(f"{name} does not hand-stamp ts on event appends (FS-03)",
              not re.search(r'"ts"\s*:\s*_now_iso\(\)', src))

    # ---- FS-04: seq high-water regression guard ---------------------------
    from atomic_write import (atomic_append_jsonl, check_substrate_regression,
                              SubstrateRegressionError, _read_seqhw)
    ws = _mk_ws()
    ep = os.path.join(ws, "_hq", "data", "events.jsonl")
    for _ in range(3):
        atomic_append_jsonl(ep, [{"type": "pack_run", "source_skill": "t", "data": {}}])
    check("sidecar records the high-water", _read_seqhw(Path(ep)) == 3,
          str(_read_seqhw(Path(ep))))
    # clobber: rewrite with only the first (lower-seq) line
    first = open(ep, encoding="utf-8").readlines()[0]
    open(ep, "w", encoding="utf-8").write(first)
    raised = False
    try:
        atomic_append_jsonl(ep, [{"type": "pack_run", "source_skill": "t", "data": {}}])
    except SubstrateRegressionError:
        raised = True
    check("regressed log refuses the in-place append + raises", raised)
    marker = check_substrate_regression(ep)
    check("regression marker written", marker is not None and marker["n_quarantined"] == 1)
    import glob
    q = glob.glob(os.path.join(ws, "_hq", "data", "*.quarantine-*"))
    check("batch quarantined, not lost", len(q) == 1)
    # the live log was NOT clobbered further (still the single stale line)
    check("in-place append refused (log unchanged)",
          len(open(ep, encoding="utf-8").readlines()) == 1)
    # disable escape hatch
    os.environ["CR_SEQ_HIGHWATER"] = "0"
    try:
        atomic_append_jsonl(ep, [{"type": "pack_run", "source_skill": "t", "data": {}}])
        check("CR_SEQ_HIGHWATER=0 disables the guard", True)
    except SubstrateRegressionError:
        check("CR_SEQ_HIGHWATER=0 disables the guard", False)
    finally:
        os.environ.pop("CR_SEQ_HIGHWATER", None)

    # ---- SYNC1 A2: check_substrate_regression truth-checks the marker -----
    # A healthy file with a lingering marker (row-17 shape) self-archives on
    # read and returns None — a stale marker can't keep surfacing a false alarm.
    ws_tc = _mk_ws()
    ep_tc = os.path.join(ws_tc, "_hq", "data", "events.jsonl")
    for _ in range(5):  # file max seq 5, healthy
        atomic_append_jsonl(ep_tc, [{"type": "pack_run", "source_skill": "t", "data": {}}])
    marker_tc = Path(ep_tc).with_name("events.jsonl.seqregression.json")
    marker_tc.write_text(json.dumps({
        "detected": "2026-01-01T00:00:00Z", "file_max_seq": 1, "sidecar_max_seq": 3,
        "n_quarantined": 1, "quarantine_path": "/x/q.jsonl", "holder": "t"}), encoding="utf-8")
    check("SYNC1 truth-check: resolved marker returns None",
          check_substrate_regression(ep_tc) is None)
    check("SYNC1 truth-check: resolved marker archived off live path", not marker_tc.exists())
    # a STILL-TRUE marker (sidecar above the live file max) is returned unchanged
    marker_tc.write_text(json.dumps({
        "detected": "2026-01-01T00:00:00Z", "file_max_seq": 1, "sidecar_max_seq": 999,
        "n_quarantined": 1, "quarantine_path": "/x/q.jsonl", "holder": "t"}), encoding="utf-8")
    check("SYNC1 truth-check: still-true marker returned",
          (check_substrate_regression(ep_tc) or {}).get("sidecar_max_seq") == 999)

    # ---- SYNC1 A1: read-side stale-view detection ------------------------
    from atomic_write import events_freshness, _write_seqhw
    import substrate_health as _sh
    ws_sv = _mk_ws()
    ep_sv = os.path.join(ws_sv, "_hq", "data", "events.jsonl")
    for _ in range(3):
        atomic_append_jsonl(ep_sv, [{"type": "pack_run", "source_skill": "t", "data": {}}])
    check("SYNC1 stale-view: healthy view not regressed",
          events_freshness(ep_sv)["regressed"] is False)
    check("SYNC1 stale-view: check_stale_view None when healthy",
          _sh.check_stale_view(ws_sv) is None)
    _write_seqhw(Path(ep_sv), 4606)  # sidecar ahead of the file we can see
    check("SYNC1 stale-view: regressed view detected", events_freshness(ep_sv)["regressed"] is True)
    check("SYNC1 stale-view: check_stale_view returns the freshness dict",
          (_sh.check_stale_view(ws_sv) or {}).get("regressed") is True)
    check("SYNC1 stale-view: substrate_alarm_lines carries the stale-view line",
          any("behind its own high-water" in ln for ln in _sh.substrate_alarm_lines(ws_sv)))

    # ---- FS-05/15 + FS-06: substrate_health ------------------------------
    import substrate_health as sh
    ws2 = _mk_ws()
    ep2 = os.path.join(ws2, "_hq", "data", "events.jsonl")
    atomic_append_jsonl(ep2, [{"type": "pack_run", "source_skill": "t", "data": {}}])
    check("healthy workspace → no alarms", sh.substrate_alarm_lines(ws2) == [])
    # corrupt entities.json → loud
    open(os.path.join(ws2, "_hq", "data", "entities.json"), "w").write("{ truncated")
    bad = sh.check_json_parse(ws2)
    check("unparseable entities.json surfaced (FS-15)",
          any(b["file"] == "entities.json" for b in bad))
    check("corruption produces a LOUD alarm line",
          any("couldn't read" in ln for ln in sh.substrate_alarm_lines(ws2)))
    # duplicate seq (FS-06)
    open(ep2, "a", encoding="utf-8").write(
        json.dumps({"type": "pack_run", "seq": 1, "ts": "x", "data": {}}) + "\n")
    dup = sh.check_duplicate_seqs(ws2)
    check("duplicate seq counted (FS-06)", dup["n_duplicated"] == 1 and 1 in dup["dupes"])

    # ---- FS-07: schedule_created on the add flow (instruction pin) --------
    reg = (ROOT / "skills" / "enable-command-room-schedules" / "SKILL.md").read_text(encoding="utf-8")
    check("registration mandates schedule_created on EVERY path (FS-07)",
          "schedule_created" in reg and "EVERY registration path" in reg)
    check("add-flow branch names the schedule_created write (FS-07)",
          "Substrate write on add (FS-07" in reg)

    # ---- FS-01: bridge Phase 4.7 on same-version re-fire ------------------
    bridge = (ROOT / "skills" / "command-room-update-bridge" / "SKILL.md").read_text(encoding="utf-8")
    check("bridge runs Phase 4.7 state-gated blocks before up-to-date exit (FS-01)",
          "FS-01" in bridge and "state-gated" in bridge)

    if failures:
        print(f"\nsubstrate integrity FAIL — {len(failures)} of {checks} failed")
        return 1
    print(f"substrate integrity (FS-01/03/04/05/06/07/15): {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
