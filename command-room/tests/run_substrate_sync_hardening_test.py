#!/usr/bin/env python3
"""SPEC SYNC1 — substrate sync hardening (stale-mount reads + self-healing
writer + alarm lifecycle).

Covers the ten binary acceptance criteria in §6. Fixtures mirror the SHAPES of
the archived real artifacts in `_hq/data/_recovery_rollback_20260719/`
(seqregression marker file_max 3591 / sidecar 4606; a commitment_resolved and a
sent_reconcile quarantine receipt stamped seq 3592) but are reconstructed as
date-relative synthetic fixtures with placeholder content — never real
workspace rows (the real-name guard + Category denylist are live). Every date
literal here is deliberately PAST (2026-01-xx), so G14 stays clean.

House convention: non-zero exit = fail.
"""
from __future__ import annotations

import glob
import json
import os
import sys
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


def _mk_ws() -> tuple[str, str]:
    d = tempfile.mkdtemp()
    dd = os.path.join(d, "_hq", "data")
    os.makedirs(dd)
    return d, dd


# Canonical minimal receipt payload (a real receipt shape, placeholder content).
_D = {"task_id": "inbox", "kind": "inbox", "status": "complete", "fired_via": "scheduled"}


def _seed(dd: str, n: int) -> str:
    """A synthetic events.jsonl of n pack_run rows, seqs 1..n, past ts."""
    ep = os.path.join(dd, "events.jsonl")
    lines = [
        json.dumps({"type": "pack_run", "source_skill": "t",
                    "ts": "2026-01-01T00:00:0%dZ" % (i % 10), "seq": i, "data": dict(_D)})
        for i in range(1, n + 1)
    ]
    Path(ep).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return ep


def _write_quarantine_fixtures(dd: str) -> None:
    """Mirror the two archived quarantine shapes: a commitment_resolved receipt
    and a sent_reconcile receipt, both stamped seq 3592 (placeholder content)."""
    q1 = os.path.join(dd, "events.jsonl.quarantine-20260101T010101010101Z.jsonl")
    Path(q1).write_text(json.dumps({
        "type": "commitment_resolved", "source_skill": "reconcile-sent",
        "ts": "2026-01-01T01:00:00Z", "seq": 3592,
        "data": {"commitment_id": "commitment_seq_1234", "resolved_by": "sent_reconcile",
                 "evidence": "placeholder — matched a sent message", "resolution": "done"},
    }) + "\n", encoding="utf-8")
    q2 = os.path.join(dd, "events.jsonl.quarantine-20260101T020202020202Z.jsonl")
    Path(q2).write_text(json.dumps({
        "type": "sent_reconcile", "source_skill": "reconcile-sent",
        "ts": "2026-01-01T02:00:00Z", "seq": 3592,
        "data": dict(_D, task_id="reconcile-sent", kind="reconcile-sent",
                     n_closed=0, n_pending=0, machine="synthetic"),
    }) + "\n", encoding="utf-8")


def main() -> int:  # noqa: C901 — one integration suite, sectioned by criterion
    from atomic_write import (SubstrateRegressionError, atomic_append_jsonl,
                              check_substrate_regression, events_freshness,
                              read_rev_sidecar, _write_seqhw, _file_max_seq,
                              atomic_write_json_locked)
    import substrate_health as sh
    from alarm_artifacts import sweep_alerts, write_alert
    import reconcile_forward as rf

    # ================= Criterion 2 — stale-view fixture =================
    ws, dd = _mk_ws()
    ep = _seed(dd, 3591)
    _write_seqhw(Path(ep), 4606)  # the row-17 shape: file 3591 < sidecar 4606

    # (2a) substrate_alarm_lines contains the stale-view warn line
    lines = sh.substrate_alarm_lines(ws)
    check("2a stale-view alarm line present",
          any("behind its own high-water" in ln for ln in lines), str(lines))

    # (2b) writer still refuses + quarantines (FS-04 regression-kept)
    raised = False
    try:
        atomic_append_jsonl(ep, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    except SubstrateRegressionError:
        raised = True
    check("2b writer refuses stale-view append (FS-04 kept)", raised)
    check("2b batch quarantined", len(glob.glob(os.path.join(dd, "*.quarantine-*"))) >= 1)

    # (2c) preflight returns not-ok after exactly 3 retries, zero bytes written
    ws2, dd2 = _mk_ws()
    ep2 = _seed(dd2, 3591)
    _write_seqhw(Path(ep2), 4606)
    before = Path(ep2).read_bytes()
    r = sh.preflight_freshness(ws2, backoff_s=0)
    after = Path(ep2).read_bytes()
    check("2c preflight not-ok", r["ok"] is False, str(r))
    check("2c preflight retries_used == 3 exactly", r["retries_used"] == 3, str(r["retries_used"]))
    check("2c zero bytes written to events.jsonl", before == after)
    check("2c .mount_stale.json sidecar written (not an events append)",
          os.path.exists(ep2 + ".mount_stale.json"))

    # (2d) the generated alert .md: both-hypotheses wording, no bare data-loss
    alerts = glob.glob(os.path.join(ws2, "_hq", "SUBSTRATE_REGRESSION_ALERT_*.md"))
    check("2d preflight rendered an alert .md", len(alerts) == 1, str(alerts))
    alert_txt = Path(alerts[0]).read_text(encoding="utf-8") if alerts else ""
    low = alert_txt.lower()
    check("2d alert states BOTH hypotheses (stale view + real clobber)",
          "stale view" in low and "real clobber" in low)
    check("2d alert makes NO bare data-loss assertion (no 'missing'/'lost')",
          "missing" not in low and "lost" not in low, alert_txt[:200])
    check("2d alert self-clear footer present", "self-clears" in low)

    # ================= Criterion 3 — alarm-outlived-truth =================
    ws3, dd3 = _mk_ws()
    ep3 = _seed(dd3, 4643)          # HEALTHY file (max 4643 >= marker sidecar 4606)
    _write_seqhw(Path(ep3), 4643)
    marker = {"detected": "2026-01-02T00:00:00Z", "file_max_seq": 3591,
              "sidecar_max_seq": 4606, "n_quarantined": 1,
              "quarantine_path": "/x/events.jsonl.quarantine-x.jsonl",
              "holder": "atomic_append_jsonl"}
    marker_path = os.path.join(dd3, "events.jsonl.seqregression.json")
    Path(marker_path).write_text(json.dumps(marker), encoding="utf-8")
    # truth-check returns None + archives the marker
    res = check_substrate_regression(ep3)
    check("3 check_substrate_regression truth-checks to None", res is None)
    check("3 lingering marker archived off the live path", not os.path.exists(marker_path))
    check("3 marker snapshot preserved in _recovery_*",
          len(glob.glob(os.path.join(dd3, "_recovery_*", "*.seqregression.json"))) >= 1)
    # sweep_alerts banners + moves a live alert whose predicate resolved
    Path(marker_path).write_text(json.dumps(marker), encoding="utf-8")  # re-plant for sweep
    write_alert(ws3, marker)
    swept = sweep_alerts(ws3)
    check("3 sweep resolved the alert", any(s["action"] == "resolved-archived" for s in swept), str(swept))
    moved = glob.glob(os.path.join(ws3, "_hq", "_resolved_alerts", "*.md"))
    check("3 alert moved to _hq/_resolved_alerts/", len(moved) == 1)
    check("3 resolved alert carries the ✅ RESOLVED banner",
          bool(moved) and "RESOLVED" in Path(moved[0]).read_text(encoding="utf-8"))
    evs = [json.loads(l) for l in open(ep3, encoding="utf-8") if l.strip()]
    cleared = [e for e in evs if e.get("type") == "substrate_alarm_cleared"]
    check("3 substrate_alarm_cleared appended + gate-valid", len(cleared) == 1)
    check("3 substrate_alarm_cleared has seq + ts (gate-stamped)",
          bool(cleared) and isinstance(cleared[0].get("seq"), int) and cleared[0].get("ts"))
    # a STILL-TRUE alert is left in place with last_verified bumped
    ws3b, dd3b = _mk_ws()
    ep3b = _seed(dd3b, 100)
    _write_seqhw(Path(ep3b), 4606)  # still regressed
    mk2 = dict(marker)
    ap_live = write_alert(ws3b, mk2)
    import time as _t
    v0 = json.loads(__import__("re").search(r"\{.*?\}", ap_live.read_text(encoding="utf-8"), 16).group(0))["last_verified"]
    _t.sleep(0.01)
    sweep_alerts(ws3b)
    still_there = ap_live.exists()
    v1 = json.loads(__import__("re").search(r"\{.*?\}", ap_live.read_text(encoding="utf-8"), 16).group(0))["last_verified"] if still_there else None
    check("3 still-true alert left in place", still_there)
    check("3 still-true alert last_verified bumped", v1 is not None and v1 != v0)

    # ================= Criterion 4 — quarantine replay =================
    ws4, dd4 = _mk_ws()
    ep4 = _seed(dd4, 100)  # healthy log (no seqhw regression)
    _write_quarantine_fixtures(dd4)
    # plant a duplicate-by-data.id commitment quarantine
    qdup = os.path.join(dd4, "events.jsonl.quarantine-20260101T030303030303Z.jsonl")
    Path(qdup).write_text(
        json.dumps({"type": "commitment", "source_skill": "x", "ts": "2026-01-01T03:00:00Z",
                    "data": {"id": "cmt_DUP1", "kind": "task", "title": "orig"}}) + "\n" +
        json.dumps({"type": "commitment", "source_skill": "x", "ts": "2026-01-01T03:00:01Z",
                    "data": {"id": "cmt_DUP1", "kind": "task", "title": "planted dup"}}) + "\n",
        encoding="utf-8")
    atomic_append_jsonl(ep4, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    evs4 = [json.loads(l) for l in open(ep4, encoding="utf-8") if l.strip()]
    cr_ = [e for e in evs4 if e.get("type") == "commitment_resolved"]
    sr_ = [e for e in evs4 if e.get("type") == "sent_reconcile"]
    check("4 commitment_resolved quarantine replayed once", len(cr_) == 1)
    check("4 sent_reconcile quarantine replayed once", len(sr_) == 1)
    check("4 replayed events got FRESH seqs > file max",
          bool(cr_) and cr_[0]["seq"] > 100 and sr_[0]["seq"] > 100)
    check("4 replayed event kept ORIGINAL ts",
          bool(cr_) and cr_[0]["ts"] == "2026-01-01T01:00:00Z")
    dups = [e for e in evs4 if e.get("data", {}).get("id") == "cmt_DUP1"]
    check("4 commitment dedup by data.id (planted dup NOT double-appended)", len(dups) == 1, str(len(dups)))
    recon = [e for e in evs4 if e.get("type") == "substrate_reconciled"]
    check("4 exactly one substrate_reconciled receipt", len(recon) == 1)
    check("4 quarantines archived off the live path",
          len(glob.glob(os.path.join(dd4, "*.quarantine-*"))) == 0)
    check("4 quarantines snapshot-preserved in _recovery_*",
          len(glob.glob(os.path.join(dd4, "_recovery_*", "*.quarantine-*"))) >= 1)
    # idempotence: an immediate second append reconciles nothing
    content_before = Path(ep4).read_text(encoding="utf-8")
    atomic_append_jsonl(ep4, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    evs4b = [json.loads(l) for l in open(ep4, encoding="utf-8") if l.strip()]
    check("4 idempotent: still exactly one substrate_reconciled",
          sum(1 for e in evs4b if e.get("type") == "substrate_reconciled") == 1)

    # ================= Criterion 5 — merge-forward + red path + mutation =====
    # SUCCESS: live 3591, conflict copy (events (1).jsonl) max 4700, seqhw 4606
    ws5, dd5 = _mk_ws()
    ep5 = _seed(dd5, 3591)
    _write_seqhw(Path(ep5), 4606)
    with open(ep5, "a", encoding="utf-8") as f:  # a unique straggler in the stale live
        f.write(json.dumps({"type": "note", "source_skill": "t",
                            "ts": "2026-01-01T09:09:09Z", "seq": 3590,
                            "data": {"x": "straggler"}}) + "\n")
    cc = os.path.join(dd5, "events (1).jsonl")
    Path(cc).write_text("\n".join(
        json.dumps({"type": "pack_run", "source_skill": "t",
                    "ts": "2026-01-01T00:00:00Z", "seq": i, "data": dict(_D)})
        for i in range(1, 4701)) + "\n", encoding="utf-8")
    atomic_append_jsonl(ep5, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    ev5 = [json.loads(l) for l in open(ep5, encoding="utf-8") if l.strip()]
    maxseq5 = max(e["seq"] for e in ev5 if isinstance(e.get("seq"), int) and e["seq"] < 10**10)
    strag = [e for e in ev5 if e.get("data", {}).get("x") == "straggler"]
    check("5 winner promoted (max seq >= 4700)", maxseq5 >= 4700, str(maxseq5))
    check("5 straggler re-seqed above the winner max", bool(strag) and strag[0]["seq"] > 4700)
    check("5 .seqhw advanced past 4700", (read_seqhw := __import__("atomic_write")._read_seqhw)(Path(ep5)) >= 4700)
    check("5 snapshots of touched files exist", len(glob.glob(os.path.join(dd5, "_recovery_*", "*"))) > 0)

    # RED PATH: regressed, NO candidate reaches 4606 -> raise, live byte-unchanged
    ws5b, dd5b = _mk_ws()
    ep5b = _seed(dd5b, 3591)
    _write_seqhw(Path(ep5b), 4606)
    q = os.path.join(dd5b, "events.jsonl.quarantine-20260101T040404040404Z.jsonl")
    Path(q).write_text(json.dumps({"type": "pack_run", "source_skill": "t",
                                   "ts": "2026-01-01T04:00:00Z", "seq": 3592, "data": dict(_D)}) + "\n",
                       encoding="utf-8")
    live_before = Path(ep5b).read_bytes()
    raised5 = False
    try:
        atomic_append_jsonl(ep5b, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    except SubstrateRegressionError:
        raised5 = True
    check("5 red path: no candidate reaches hw -> SubstrateRegressionError raised", raised5)
    check("5 red path: live file byte-unchanged (no candidate promoted)",
          Path(ep5b).read_bytes() == live_before)

    # MUTATION: remove the load-bearing rule -> reconciler becomes the clobberer
    ws5c, dd5c = _mk_ws()
    ep5c = _seed(dd5c, 3591)
    _write_seqhw(Path(ep5c), 4606)
    cc2 = os.path.join(dd5c, "events (1).jsonl")  # stale conflict copy at 3595 (< hw)
    Path(cc2).write_text("\n".join(
        json.dumps({"type": "pack_run", "source_skill": "t",
                    "ts": "2026-01-01T00:00:00Z", "seq": i, "data": dict(_D)})
        for i in range(1, 3596)) + "\n", encoding="utf-8")
    orig_rule = rf._candidate_meets_highwater
    rf._candidate_meets_highwater = lambda cmax, hw: True  # REMOVE THE RULE
    try:
        try:
            atomic_append_jsonl(ep5c, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
        except SubstrateRegressionError:
            pass
        clobbered = __import__("atomic_write")._read_seqhw(Path(ep5c)) < 4606
        check("5 mutation: rule removed -> reconciler lowered the high-water (clobber proven)",
              clobbered)
    finally:
        rf._candidate_meets_highwater = orig_rule  # restore
    check("5 mutation: rule restored", rf._candidate_meets_highwater is orig_rule)

    # ===== Second-eyes regressions (2026-07-20 review) ====================
    # (R1) ABSENT events.jsonl + live .seqhw is REGRESSED, and the reconciler
    # must NOT rebuild the log from a quarantine at seq 1 and lower the
    # high-water (the failure direction is refuse, always).
    wsr, ddr = _mk_ws()
    epr = os.path.join(ddr, "events.jsonl")   # file deliberately ABSENT
    _write_seqhw(Path(epr), 4606)
    check("R1 absent file + live seqhw -> regressed (fail-closed direction)",
          events_freshness(epr)["regressed"] is True)
    Path(os.path.join(ddr, "events.jsonl.quarantine-20260101T050505050505Z.jsonl")).write_text(
        json.dumps({"type": "pack_run", "source_skill": "t",
                    "ts": "2026-01-01T05:00:00Z", "seq": 3592, "data": dict(_D)}) + "\n",
        encoding="utf-8")
    raised_r1 = False
    try:
        atomic_append_jsonl(epr, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    except SubstrateRegressionError:
        raised_r1 = True
    check("R1 append through the absent-file view refuses (FS-04)", raised_r1)
    hw_r1 = __import__("atomic_write")._read_seqhw(Path(epr))
    check("R1 high-water NOT silently lowered", hw_r1 is not None and hw_r1 >= 4606, str(hw_r1))

    # (R2) A preflight-only refusal (no quarantine, no marker) leaves only the
    # .mount_stale.json sidecar — the first HEALTHY append must clear it
    # (spec A4), not let it linger forever.
    wsr2, ddr2 = _mk_ws()
    epr2 = _seed(ddr2, 100)
    _write_seqhw(Path(epr2), 4606)
    sh.preflight_freshness(wsr2, backoff_s=0)
    check("R2 preflight-only refusal wrote the sidecar",
          os.path.exists(epr2 + ".mount_stale.json"))
    _write_seqhw(Path(epr2), 100)   # the view heals
    atomic_append_jsonl(epr2, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    check("R2 .mount_stale.json cleared at first healthy append",
          not os.path.exists(epr2 + ".mount_stale.json"))
    check("R2 sidecar snapshot preserved in _recovery_*",
          len(glob.glob(os.path.join(ddr2, "_recovery_*", "*.mount_stale.json"))) >= 1)

    # ================= Criterion 6 — resolver back-compat =================
    import data_root, events_io, writer_lock
    ws6, dd6 = _mk_ws()
    # golden path: every seam byte-identical to today with NO override
    check("6 resolver default == _hq/data", data_root.resolve(ws6) == Path(ws6) / "_hq" / "data")
    check("6 events_io._data_dir default byte-identical",
          events_io._data_dir(ws6) == Path(ws6) / "_hq" / "data")
    check("6 substrate_health._events_path default byte-identical",
          sh._events_path(Path(ws6)) == Path(ws6) / "_hq" / "data" / "events.jsonl")
    lp6, _ = writer_lock._resolve(ws6)
    check("6 writer_lock._resolve default byte-identical",
          lp6 == Path(ws6) / "_hq" / "data" / writer_lock.LOCK_FILENAME)
    # CR_DATA_ROOT override honored by ALL seams
    reloc = Path(tempfile.mkdtemp()) / "relocated"
    os.environ["CR_DATA_ROOT"] = str(reloc)
    try:
        check("6 CR_DATA_ROOT honored by data_root.resolve", data_root.resolve(ws6) == reloc)
        check("6 CR_DATA_ROOT honored by events_io", events_io._data_dir(ws6) == reloc)
        check("6 CR_DATA_ROOT honored by substrate_health",
              sh._events_path(Path(ws6)) == reloc / "events.jsonl")
        lp6b, _ = writer_lock._resolve(ws6)
        check("6 CR_DATA_ROOT honored by writer_lock", lp6b == reloc / writer_lock.LOCK_FILENAME)
    finally:
        os.environ.pop("CR_DATA_ROOT", None)

    # structural guard: PGUARD-style inverted grep with an allowlist of the
    # resolver itself REDs on a planted script that hardcodes _hq/data.
    def _scan_hardcoded(scripts_dir: Path, allow: set[str]) -> list[str]:
        import re
        # Matches both the `Path(x) / "_hq" / "data"` and `"_hq", "data"`
        # construction forms, plus the `_hq/data` string-literal form.
        pat = re.compile(r'["\']_hq["\']\s*[,/]\s*["\']data["\']|_hq/data')
        hits = []
        for p in scripts_dir.glob("*.py"):
            if p.name in allow:
                continue
            if pat.search(p.read_text(encoding="utf-8", errors="replace")):
                hits.append(p.name)
        return hits

    guard_dir = Path(tempfile.mkdtemp())
    # the resolver itself is the ONE allowlisted constructor
    (guard_dir / "data_root.py").write_text('p = root / "_hq" / "data"\n', encoding="utf-8")
    # a planted violator
    (guard_dir / "planted_bad.py").write_text(
        'from pathlib import Path\nx = Path(root) / "_hq" / "data" / "events.jsonl"\n', encoding="utf-8")
    hits = _scan_hardcoded(guard_dir, allow={"data_root.py"})
    check("6 structural guard REDs on a planted hardcoded _hq/data script",
          "planted_bad.py" in hits)
    check("6 structural guard allowlists the resolver itself", "data_root.py" not in hits)

    # ================= Criterion 7 — cross-machine lease =================
    ws7, dd7 = _mk_ws()
    ep7 = os.path.join(dd7, "events.jsonl")
    atomic_append_jsonl(ep7, [{"type": "pack_run", "source_skill": "t", "data": dict(_D)}])
    info = json.loads(Path(os.path.join(dd7, ".writer.lock.info")).read_text(encoding="utf-8"))
    check("7 lock info carries machine_id", isinstance(info.get("machine_id"), str) and info["machine_id"])
    check("7 lock info carries seqhw_seen key", "seqhw_seen" in info)
    mid_path = writer_lock.machine_id_path()
    check("7 machine_id file path is NOT under the workspace root",
          not str(mid_path).startswith(str(ws7)))
    check("7 machine_id stable across two acquisitions",
          writer_lock.machine_id() == writer_lock.machine_id())

    # ================= Criterion 8 — new event types + G13 grep ==========
    from event_types import load_event_types
    from event_gate import gate_events, EventGateError
    enum = load_event_types()
    check("8 substrate_reconciled registered in schema enum", "substrate_reconciled" in enum)
    check("8 substrate_alarm_cleared registered in schema enum", "substrate_alarm_cleared" in enum)
    # gate accepts them; a planted unregistered type still rejects
    gate_events([{"type": "substrate_reconciled", "source_skill": "x", "data": {"replayed": 0}}], strict_enum=True)
    rejected = False
    try:
        gate_events([{"type": "definitely_not_a_type_xyz", "source_skill": "x", "data": {}}], strict_enum=True)
    except EventGateError:
        rejected = True
    check("8 gate rejects a planted unregistered type", rejected)
    # G13: skill text references reconcile surfacing + the alert helper
    sysh = (ROOT / "skills" / "system-health" / "SKILL.md").read_text(encoding="utf-8")
    check("8 system-health SKILL references reconcile surfacing (G13)", "reconcile_forward" in sysh)
    check("8 system-health SKILL references the alert helper (G13)", "alarm_artifacts" in sysh and "write_alert" in sysh)
    sched = (ROOT / "shared" / "scripts" / "schedule_config.py").read_text(encoding="utf-8")
    check("8 maintenance prompt references preflight step 0 (G13)", "preflight_freshness" in sched)

    # ================= Criterion 9 — fixtures mirror real shapes ==========
    # the quarantine fixtures carry the archived receipt shapes (type + seq 3592)
    ws9, dd9 = _mk_ws()
    _write_quarantine_fixtures(dd9)
    qfs = sorted(glob.glob(os.path.join(dd9, "*.quarantine-*.jsonl")))
    shapes = set()
    for qf in qfs:
        for l in open(qf, encoding="utf-8"):
            if l.strip():
                e = json.loads(l)
                shapes.add(e["type"])
                check("9 quarantine fixture receipt stamped seq 3592 (archived shape)",
                      e.get("seq") == 3592)
    check("9 fixtures mirror commitment_resolved + sent_reconcile shapes",
          {"commitment_resolved", "sent_reconcile"} <= shapes)

    # ================= Criterion 10 — D-4 entities rev sidecar ============
    ws10, dd10 = _mk_ws()
    ent = os.path.join(dd10, "entities.json")
    # back-compat: absent sidecar -> no warn, no crash
    check("10 rev sidecar absent -> read_rev_sidecar None (back-compat)",
          read_rev_sidecar(ent) is None)
    # (check_entities_rev reader removed per M's F4 ruling 2026-07-21 — the
    # write-half stamping below is the surviving D-4 surface.)
    atomic_write_json_locked(ent, {"workspace": {}, "orgs": []}, holder="test")
    rev1 = read_rev_sidecar(ent)
    check("10 rev sidecar stamped on locked write", rev1 is not None and rev1.get("rev") == 1)
    atomic_write_json_locked(ent, {"workspace": {}, "orgs": [1]}, holder="test")
    rev2 = read_rev_sidecar(ent)
    check("10 rev sidecar bumps on every locked write", rev2 is not None and rev2.get("rev") == 2)
    # a non-entities file gets NO rev sidecar
    other = os.path.join(dd10, "workspace_config.json")
    atomic_write_json_locked(other, {"k": 1}, holder="test")
    check("10 rev sidecar is entities/aliases only", read_rev_sidecar(other) is None)

    # ------------------------------------------------------------------
    if failures:
        print(f"\nSYNC1 substrate-sync hardening FAIL — {len(failures)} of {checks} checks failed")
        return 1
    print(f"SYNC1 substrate-sync hardening: {checks} checks OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
