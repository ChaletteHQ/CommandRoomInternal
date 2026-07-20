#!/usr/bin/env python3
"""
Receipt-contract regression battery (v4.5.2 R1 — FINDINGS_M_v451).

Every fixture line below is a REAL receipt shape observed on M's live
workspace during the 2026-07-07/08 dogfood — copied from the findings log,
not idealized (the passes-unit-tests-crashes-on-real-data class is the known
trap; see cr-realdata-fixture gotcha). The finding each shape reproduces is
cited inline.

Acceptance (SPEC_V452 R1): a usage-report-style run count computed through
the shared reader matches the raw receipt count for all 12 scheduled tasks —
including reconcile-sent and session-sweep, whose sent_reconcile /
session_sweep_run receipts were F-49's exact miss.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import receipts as R  # noqa: E402

PASS = 0
FAIL = 0
FAILURES = []


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        FAILURES.append(label)
        print(f"  FAIL {label}  {detail}")


def make_workspace(base: Path, lines: list) -> Path:
    ws = base
    data_dir = ws / "_hq" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
        for line in lines:
            if isinstance(line, str):
                f.write(line + "\n")  # raw (malformed) line, as found on disk
            else:
                f.write(json.dumps(line) + "\n")
    return ws


# ---------------------------------------------------------------------------
# The live-substrate fixture — Jul 7-8 receipt shapes, verbatim field sets
# ---------------------------------------------------------------------------

FIXTURE = [
    # F-47 P2a morning scheduled fire: task_id 'cr-commitments', outcome not
    # status, fired_at not ts-only. (Also the F-10b matcher-miss id class.)
    {"seq": 3560, "ts": "2026-07-08T13:48:00Z", "type": "pack_run",
     "source_skill": "cr-commitments",
     "data": {"task_id": "cr-commitments", "fired_at": "2026-07-08T13:48:00Z",
              "outcome": "complete", "surfaced_total": 7,
              "open_total_after_filter": 63}},
    # F-47 P2a afternoon manual re-run of the SAME task, SAME day: kind-only
    # identity, date not fired_at, late_tier, header_counts. fired_via absent.
    {"seq": 3598, "ts": "2026-07-08T21:26:00Z", "type": "pack_run",
     "source_skill": "commitments",
     "data": {"kind": "commitments", "date": "2026-07-08", "status": "complete",
              "late_tier": "note", "surfaced": 7,
              "header_counts": {"owed_you": 52, "you_owe": 84}}},
    # F-10b: inbox receipt carries ONLY kind — no task_id anywhere. Naive-local
    # timestamp (F-15's mixed-convention class; reader must still parse it).
    {"seq": 3550, "ts": "2026-07-08T14:00:00", "type": "pack_run",
     "source_skill": "inbox",
     "data": {"kind": "inbox", "status": "complete", "items_drafted_text": 2}},
    # F-49: upcoming-meetings written with the underscore kind variant.
    {"seq": 3540, "ts": "2026-07-08T13:35:00Z", "type": "pack_run",
     "source_skill": "upcoming-meetings",
     "data": {"kind": "upcoming_meetings", "status": "complete",
              "briefs_generated": 5}},
    # F-49 + F-50 P2c: past-meetings under the cr- prefix, with lateness_tier
    # (the SAME skill wrote late_tier that morning — field-name drift per run).
    {"seq": 3600, "ts": "2026-07-08T21:47:00Z", "type": "pack_run",
     "source_skill": "cr-past-meetings",
     "data": {"task_id": "cr-past-meetings", "status": "complete",
              "lateness_tier": "note", "meetings_processed": 3}},
    # ... and the same task's earlier fire with the underscore kind spelling.
    {"seq": 3548, "ts": "2026-07-08T07:15:00Z", "type": "pack_run",
     "source_skill": "past-meetings",
     "data": {"kind": "past_meetings", "status": "complete",
              "late_tier": "note"}},
    # F-49: pulse fire that left ONLY a dont_forget_run (no pack_run) — the
    # shape the usage report missed entirely.
    {"seq": 3581, "ts": "2026-07-08T16:19:00Z", "type": "dont_forget_run",
     "source_skill": "pulse",
     "data": {"surfaced_count": 4, "suppressed_count": 2}},
    # Pulse fire that wrote BOTH its pack_run (dont_forget kind) and its
    # dont_forget_run within one fire — must count as ONE run.
    {"seq": 3620, "ts": "2026-07-07T16:19:00Z", "type": "pack_run",
     "source_skill": "cr-dont-forget",
     "data": {"kind": "dont_forget", "status": "complete"}},
    {"seq": 3621, "ts": "2026-07-07T16:20:00Z", "type": "dont_forget_run",
     "source_skill": "pulse",
     "data": {"surfaced_count": 3}},
    # F-51: the phantom re-fire's fired_via spelling (normalizes to catchup).
    {"seq": 3599, "ts": "2026-07-08T21:54:00Z", "type": "pulse_run",
     "source_skill": "pulse",
     "data": {"fired_via": "scheduled_late_refire", "note": "not re-emitted to protect counters"}},
    # Morning brief, canonical shape.
    {"seq": 3530, "ts": "2026-07-08T14:14:00Z", "type": "pack_run",
     "source_skill": "morning-brief",
     "data": {"task_id": "morning-brief", "outcome": "complete",
              "needs_attention_ids": ["cmt_x1", "cmt_x2"]}},
    # F-49: reconcile-sent receipts — sent_reconcile type, NO pack_run ever.
    # (Two runs; the 0-yield shape is the lifetime pattern per F-47 obs.)
    {"seq": 3520, "ts": "2026-07-07T17:28:00Z", "type": "sent_reconcile",
     "source_skill": "morning-briefing",
     "data": {"cursor_from": "2026-07-06T17:00:00Z", "cursor_to": "2026-07-07T17:28:00Z",
              "sent_scanned_count": 12, "n_closed": 0, "n_pending": 0}},
    {"seq": 3570, "ts": "2026-07-08T13:09:00Z", "type": "sent_reconcile",
     "source_skill": "reconcile-sent",
     "data": {"cursor_from": "2026-07-07T17:28:00Z", "cursor_to": "2026-07-08T13:09:00Z",
              "sent_scanned_count": 15, "n_closed": 0, "n_pending": 0}},
    # F-49: session-sweep receipts. Two manual runs 4 minutes apart (F-08's
    # 18:10/18:14 double-run) — SAME type, must count as TWO runs; plus the
    # F-33 late nightly run with the wrong window label (metadata inaccuracy
    # — still a valid receipt).
    {"seq": 3500, "ts": "2026-07-07T18:10:00Z", "type": "session_sweep_run",
     "source_skill": "session-sweep",
     "data": {"sessions_scanned": 2, "events_recovered": 1, "skipped_dedup": 0,
              "window": "last-24h", "window_hours": 24}},
    {"seq": 3502, "ts": "2026-07-07T18:14:00Z", "type": "session_sweep_run",
     "source_skill": "session-sweep",
     "data": {"sessions_scanned": 1, "events_recovered": 0, "skipped_dedup": 0,
              "window": "last-24h", "window_hours": 24}},
    {"seq": 3549, "ts": "2026-07-08T07:11:21Z", "type": "session_sweep_run",
     "source_skill": "session-sweep",
     "data": {"sessions_scanned": 2, "events_recovered": 0, "skipped_dedup": 0,
              "window": "last-24h", "window_hours": 24}},
    # cleanup: one legacy audit_run + one current cleanup_run.
    {"seq": 2100, "ts": "2026-06-28T01:00:00Z", "type": "audit_run",
     "source_skill": "cleanup", "data": {"actions_taken": []}},
    {"seq": 3610, "ts": "2026-07-05T01:00:00Z", "type": "cleanup_run",
     "source_skill": "cleanup",
     "data": {"actions_taken": [], "items_flagged_for_user": [], "tail_hash": "abc"}},
    # F-36/F-09: monthly-report fire — ONE operator report + TWO byte-identical
    # month value receipts 17s apart + a quarter receipt. ONE run, not four.
    {"seq": 3410, "ts": "2026-07-01T15:03:30Z", "type": "operator_report_generated",
     "source_skill": "operator-report",
     "data": {"window": "2026-06-01..2026-07-01", "hours_estimate": 14}},
    {"seq": 3411, "ts": "2026-07-01T15:03:45Z", "type": "value_receipt_generated",
     "source_skill": "value-receipt", "data": {"window": "month", "period": "2026-06"}},
    {"seq": 3412, "ts": "2026-07-01T15:04:07Z", "type": "value_receipt_generated",
     "source_skill": "value-receipt", "data": {"window": "month", "period": "2026-06"}},
    {"seq": 3413, "ts": "2026-07-01T15:04:20Z", "type": "value_receipt_generated",
     "source_skill": "value-receipt", "data": {"window": "quarter", "period": "2026-Q2"}},
    # friday-wrap + weekly-insights + relationship-moves + commitment-triage +
    # dormant-scan, canonical v4.5.2 shapes (the receiptless four get receipts).
    {"seq": 3630, "ts": "2026-07-08T07:20:00Z", "type": "pack_run",
     "source_skill": "friday-wrap",
     "data": {"task_id": "friday-wrap", "kind": "friday-wrap", "status": "complete",
              "fired_via": "catchup"}},
    {"seq": 3631, "ts": "2026-07-06T02:00:00Z", "type": "pack_run",
     "source_skill": "weekly-insights",
     "data": {"task_id": "weekly-insights", "kind": "weekly-insights",
              "status": "complete", "fired_via": "scheduled"}},
    {"seq": 3632, "ts": "2026-07-06T00:00:00Z", "type": "pack_run",
     "source_skill": "relationship-moves",
     "data": {"task_id": "relationship-moves", "status": "complete"}},
    {"seq": 3633, "ts": "2026-07-03T22:00:00Z", "type": "pack_run",
     "source_skill": "commitment-triage",
     "data": {"task_id": "commitment-triage", "status": "complete",
              "fired_via": "user-trigger"}},
    {"seq": 3634, "ts": "2026-07-08T02:00:00Z", "type": "pack_run",
     "source_skill": "dormant-customer-scan",
     "data": {"task_id": "dormant-customer-scan", "status": "complete",
              "fired_via": "manual", "flagged_entity_ids": ["org_9"]}},
    # NON-receipt noise that must NOT be counted anywhere: a show-my-list
    # fire-marker, an on-demand follow-up pack, a weekly_recap_run (the
    # skill's own event — friday-wrap's receipt is the pack_run above), a
    # late_fire event, and a plain commitment.
    {"seq": 3700, "ts": "2026-07-08T15:00:00Z", "type": "pack_run",
     "source_skill": "show-my-list",
     "data": {"kind": "list", "items_surfaced": 5, "fired_via": "user-trigger"}},
    {"seq": 3701, "ts": "2026-07-08T15:05:00Z", "type": "pack_run",
     "source_skill": "follow-up-ritual", "data": {"kind": "follow_up_pack"}},
    {"seq": 3702, "ts": "2026-07-08T07:20:30Z", "type": "weekly_recap_run",
     "source_skill": "weekly-recap", "data": {"window_days": 7}},
    {"seq": 3703, "ts": "2026-07-08T07:15:00Z", "type": "late_fire",
     "source_skill": "past-meetings",
     "data": {"taskId": "past-meetings", "tier": "note", "lateness_minutes": 430}},
    {"seq": 3704, "ts": "2026-07-08T16:00:00Z", "type": "commitment",
     "source_skill": "commitments",
     "data": {"id": "cmt_zz", "title": "x", "kind": "promise", "status": "open"}},
    # Malformed lines exactly as the corruption class appears on disk — the
    # reader must skip, never crash (Sub-bug #14b family).
    '{"seq": 3705, "ts": "2026-07-08T16:01:00Z", "type": "pack_run", "data": {"kind": "inbox"',
    '"just a stray top-level string"',
]

# Raw receipt truth for the fixture, counted by hand from the lines above.
EXPECTED_RUNS = {
    "morning-brief": 1,
    "upcoming-meetings": 1,
    "inbox": 1,
    "commitments": 2,        # scheduled morning + manual afternoon (F-47 P2a)
    "pulse": 3,              # dont_forget_run-only fire + (pack_run+dfr pair = 1) + late-refire pulse_run
    "past-meetings": 2,      # underscore-kind fire + cr-prefixed lateness_tier fire
    "friday-wrap": 1,
    "cleanup": 2,            # legacy audit_run + current cleanup_run
    "reconcile-sent": 2,     # F-49: was MISSING from the usage table entirely
    "monthly-report": 1,     # 1 operator report; 3 value receipts are not runs
    "weekly-insights": 1,
    "session-sweep": 3,      # F-49: was MISSING; two 4-min-apart manual runs stay 2
    "relationship-moves": 1,
    "commitment-triage": 1,
    "dormant-scan": 1,
}

# The 12 registered scheduled tasks on M's live workspace (FINDINGS F-11/F-39).
TWELVE_TASKS = [
    "morning-brief", "upcoming-meetings", "inbox", "commitments", "pulse",
    "past-meetings", "friday-wrap", "cleanup", "reconcile-sent",
    "monthly-report", "weekly-insights", "session-sweep",
]


def main() -> int:
    print("== normalization (the id-anarchy class: F-10b / F-49 / F-50)")
    for raw, want in [
        ("cr-commitments", "commitments"), ("cr-past-meetings", "past-meetings"),
        ("past_meetings", "past-meetings"), ("upcoming_meetings", "upcoming-meetings"),
        ("dont_forget", "pulse"), ("cr-dont-forget", "pulse"),
        ("cr-inbox-pulse", "pulse"), ("morning-briefing", "morning-brief"),
        ("weekly_recap", "friday-wrap"), ("dormant-customer-scan", "dormant-scan"),
        ("commitments", "commitments"), ("PAST-MEETINGS", "past-meetings"),
    ]:
        check(f"normalize_task_id({raw!r}) == {want!r}",
              R.normalize_task_id(raw) == want, repr(R.normalize_task_id(raw)))
    check("None passes through", R.normalize_task_id(None) is None)
    check("unknown id normalizes without being forced canonical",
          R.normalize_task_id("historical_backfill") == "historical-backfill")

    print("== fired_via vocabulary (R2 builds on this)")
    check("user-trigger → manual", R.normalize_fired_via("user-trigger") == "manual")
    check("scheduled_late_refire → catchup (F-51's spelling)",
          R.normalize_fired_via("scheduled_late_refire") == "catchup")
    check("scheduled passes", R.normalize_fired_via("scheduled") == "scheduled")
    check("absent → None", R.normalize_fired_via(None) is None)

    print("== late_tier is THE name; legacy spellings read forever (F-50 P2c)")
    check("late_tier read", R.get_late_tier({"late_tier": "note"}) == "note")
    check("lateness_tier read", R.get_late_tier({"lateness_tier": "note"}) == "note")
    check("bare tier read", R.get_late_tier({"tier": "degrade"}) == "degrade")
    check("late_tier wins over lateness_tier",
          R.get_late_tier({"late_tier": "note", "lateness_tier": "degrade"}) == "note")
    check("absent → None", R.get_late_tier({}) is None)

    with tempfile.TemporaryDirectory(prefix="receipts_r1_") as td:
        ws = make_workspace(Path(td) / "ws", FIXTURE)

        print("== shared reader over the live-substrate fixture")
        rec = R.iter_receipts(ws)
        check("malformed lines skipped, reader does not crash", len(rec) > 0)
        by_task = {}
        for r in rec:
            by_task.setdefault(r["task_id"], []).append(r)
        check("kind-only inbox receipt matched (F-10b)",
              len(by_task.get("inbox", [])) == 1, repr(by_task.get("inbox")))
        check("both commitments shapes matched as one task (F-47 P2a)",
              len(by_task.get("commitments", [])) == 2, repr(len(by_task.get("commitments", []))))
        check("cr-past-meetings + past_meetings unify (F-49/F-50)",
              len(by_task.get("past-meetings", [])) == 2)
        check("sent_reconcile receipts found (F-49 missing family #1)",
              len(by_task.get("reconcile-sent", [])) == 2)
        check("session_sweep_run receipts found (F-49 missing family #2)",
              len(by_task.get("session-sweep", [])) == 3)
        check("show-my-list fire-marker NOT matched to any task",
              not any(r["raw"].get("source_skill") == "show-my-list" for r in rec))
        check("weekly_recap_run NOT a friday-wrap receipt (no double count)",
              all(r["type"] != "weekly_recap_run" for r in rec))
        check("late_fire events are not receipts",
              all(r["type"] != "late_fire" for r in rec))
        pm = by_task.get("past-meetings", [])
        check("lateness_tier coalesced onto late_tier at read (F-50 P2c)",
              sorted(x["late_tier"] for x in pm) == ["note", "note"], repr(pm))
        triage = by_task.get("commitment-triage", [])
        check("legacy user-trigger fired_via reads as manual",
              triage and triage[0]["fired_via"] == "manual")
        refire = [r for r in by_task.get("pulse", []) if r["type"] == "pulse_run"]
        check("F-51 refire fired_via reads as catchup",
              refire and refire[0]["fired_via"] == "catchup")

        print("== ACCEPTANCE: run counts match raw receipt truth, all 12 tasks")
        runs = R.count_runs(ws)
        for tid in TWELVE_TASKS:
            check(f"{tid}: reader count {runs.get(tid)} == raw {EXPECTED_RUNS[tid]}",
                  runs.get(tid) == EXPECTED_RUNS[tid], repr(runs.get(tid)))
        for tid in ("relationship-moves", "commitment-triage", "dormant-scan"):
            check(f"{tid} (later-add): {runs.get(tid)} == {EXPECTED_RUNS[tid]}",
                  runs.get(tid) == EXPECTED_RUNS[tid], repr(runs.get(tid)))
        check("a task with zero receipts reports 0, not a missing row (F-49)",
              "monthly-report" in R.count_runs(ws, task_ids=["monthly-report"],
                                               since=dt.datetime(2026, 7, 5, tzinfo=dt.timezone.utc)))
        windowed = R.count_runs(ws, since=dt.datetime(2026, 7, 8, tzinfo=dt.timezone.utc))
        check("window filter: commitments Jul-8-only count is 2",
              windowed["commitments"] == 2, repr(windowed["commitments"]))
        check("window filter: monthly-report Jul-8-only count is 0 (zero-filled)",
              windowed["monthly-report"] == 0, repr(windowed["monthly-report"]))

        print("== last_receipt_times (watchdog freshness signal)")
        latest = R.last_receipt_times(ws, TWELVE_TASKS)
        check("every one of the 12 tasks has a newest-receipt time",
              all(latest[t] is not None for t in TWELVE_TASKS),
              repr({k: v for k, v in latest.items() if v is None}))
        check("commitments newest is the afternoon manual fire",
              latest["commitments"].strftime("%H:%M") == "21:26", repr(latest["commitments"]))

    print("== writer round-trip (log_receipt → shared reader)")
    with tempfile.TemporaryDirectory(prefix="receipts_w_") as td:
        ws2 = Path(td) / "ws2"
        ev = R.log_receipt(ws2, "past_meetings", fired_via="user-trigger",
                           surfaced=3, duration_ms=1200, late_tier="note",
                           extra_data={"meetings_processed": 3})
        check("legacy spelling canonicalized at write",
              ev["data"]["task_id"] == "past-meetings" and ev["data"]["kind"] == "past-meetings")
        check("legacy fired_via canonicalized at write",
              ev["data"]["fired_via"] == "manual")
        check("late_tier written under THE name",
              ev["data"].get("late_tier") == "note" and "lateness_tier" not in ev["data"])
        back = R.iter_receipts(ws2)
        check("written receipt reads back through the shared reader",
              len(back) == 1 and back[0]["task_id"] == "past-meetings"
              and back[0]["fired_via"] == "manual" and back[0]["late_tier"] == "note",
              repr(back))
        on_disk = json.loads((ws2 / "_hq" / "data" / "events.jsonl").read_text(encoding="utf-8").strip())
        check("gate auto-stamped seq + ts",
              isinstance(on_disk.get("seq"), int) and bool(on_disk.get("ts")))
        check("machine field rides on the receipt (F-38)",
              isinstance(on_disk["data"].get("machine"), str) or on_disk["data"].get("machine") is None)
        check("extra_data cannot override contract fields",
              R.log_receipt(ws2, "inbox", fired_via="scheduled",
                            extra_data={"task_id": "HACKED", "fired_via": "nope"}
                            )["data"]["task_id"] == "inbox")

        print("== stalled-projects scan receipt (v4.5.2 C3)")
        ev_sp = R.log_receipt(ws2, "stalled-projects", fired_via="manual",
                              surfaced=2,
                              extra_data={"flagged_thread_ids": ["project_001", "project_004"],
                                          "live_check_dropped": 1})
        check("stalled-projects is a canonical receipted task",
              ev_sp["data"]["task_id"] == "stalled-projects"
              and ev_sp["type"] == "pack_run")
        back_sp = R.iter_receipts(ws2, task_ids=["stalled-projects"])
        check("stalled-projects receipt reads back with surfaced ids for nag-dedup",
              len(back_sp) == 1
              and back_sp[0]["raw"]["data"]["flagged_thread_ids"] == ["project_001", "project_004"],
              repr(back_sp))

        print("== pipeline-tracker report receipt (RCPT1 — the OBJ1-surfaced latent crash)")
        # Before RCPT1 this exact SKILL.md-mandated call raised
        # ValueError: unknown task_id 'pipeline-tracker' on every report fire.
        ev_pt = R.log_receipt(ws2, "pipeline-tracker", fired_via="manual",
                              surfaced=3,
                              extra_data={"flagged_thread_ids": ["deal_001"],
                                          "untracked": 2})
        check("pipeline-tracker is a canonical receipted task",
              ev_pt["data"]["task_id"] == "pipeline-tracker"
              and ev_pt["type"] == "pack_run")
        back_pt = R.iter_receipts(ws2, task_ids=["pipeline-tracker"])
        check("pipeline-tracker receipt reads back with surfaced ids for nag-dedup",
              len(back_pt) == 1
              and back_pt[0]["raw"]["data"]["flagged_thread_ids"] == ["deal_001"],
              repr(back_pt))

        print("== writer validation (drift is a write-time defect)")
        for label, kwargs in [
            ("unknown task_id rejected", dict(task_id="not-a-task")),
            ("wrong receipt type for task rejected",
             dict(task_id="inbox", receipt_type="sent_reconcile")),
            ("unknown fired_via rejected", dict(task_id="inbox", fired_via="whenever")),
            ("negative surfaced rejected", dict(task_id="inbox", surfaced=-1)),
        ]:
            try:
                R.log_receipt(ws2, kwargs.pop("task_id"), **kwargs)
                check(label, False, "no ValueError raised")
            except ValueError:
                check(label, True)

    print("== SKILL.md-mandated log_receipt ids exist in the vocabulary (RCPT1 guard)")
    # The bug class this guards: a skill text mandates
    # `log_receipt(WORKSPACE_ROOT, "<id>", ...)` on every fire, but the id
    # was never registered in CANONICAL_TASK_IDS/RECEIPT_TYPES — so the
    # mandated call is a guaranteed runtime ValueError that no unit test of
    # receipts.py alone can see (pipeline-tracker shipped this way; the
    # instruction-layer-gap class). Grep the instruction layer, validate
    # each mandated id against the writer's own vocabulary.
    _mandate_re = re.compile(r"log_receipt\(\s*[A-Za-z_][A-Za-z0-9_]*\s*,\s*['\"]([A-Za-z0-9_-]+)['\"]")
    _any_call_re = re.compile(r"log_receipt\(")
    mandated: dict[str, list[str]] = {}
    unparseable: list[str] = []
    for md in sorted((ROOT / "skills").rglob("*.md")):
        text = md.read_text(encoding="utf-8")
        starts = set()
        for m in _mandate_re.finditer(text):
            starts.add(m.start())
            mandated.setdefault(m.group(1), []).append(str(md.relative_to(ROOT)))
        # Every log_receipt( site must parse as a mandate the id-extractor
        # understands — a keyword-form (`task_id="x"`) or wrapped-first-arg
        # (`str(ws)`) mandate would otherwise ESCAPE the id validation below
        # silently, which is the exact bug class this guard exists to close.
        for loose in _any_call_re.finditer(text):
            if loose.start() not in starts:
                line_no = text.count("\n", 0, loose.start()) + 1
                unparseable.append(f"{md.relative_to(ROOT)}:{line_no}")
    check("instruction layer mandates at least one log_receipt id (grep not broken)",
          len(mandated) >= 2, repr(sorted(mandated)))
    check("every skill-text log_receipt( site parses as a canonical mandate "
          "(positional workspace var, then quoted id)",
          not unparseable, f"unparseable mandate sites: {unparseable}")
    for raw_id, sources in sorted(mandated.items()):
        canonical = R.normalize_task_id(raw_id)
        check(f"mandated id {raw_id!r} is a canonical task id",
              canonical in R.CANONICAL_TASK_IDS,
              f"normalized {canonical!r} missing from CANONICAL_TASK_IDS; mandated in {sources}")
        check(f"mandated id {raw_id!r} has a registered receipt shape",
              canonical in R.RECEIPT_TYPES,
              f"no RECEIPT_TYPES entry for {canonical!r}; mandated in {sources}")

    print("== log_pack_run back-compat wrapper")
    with tempfile.TemporaryDirectory(prefix="receipts_lpr_") as td:
        from log_pack_run import log_pack_run
        ws3 = Path(td) / "ws3"
        ev = log_pack_run(workspace_root=ws3, kind="dont_forget", surfaced=4,
                          duration_ms=900, source_skill="cr-dont-forget",
                          fired_via="user-trigger")
        check("legacy kind routes to the canonical task (dont_forget → pulse)",
              ev["data"]["task_id"] == "pulse" and ev["data"]["fired_via"] == "manual")
        ev2 = log_pack_run(workspace_root=ws3, kind="list", surfaced=5,
                           duration_ms=100, source_skill="show-my-list",
                           fired_via="manual")
        check("non-task fire-marker (show-my-list 'list') still writes",
              ev2["data"]["kind"] == "list" and ev2["data"]["fired_via"] == "manual")
        check("non-task fire-marker is NOT a scheduled-task receipt",
              R.count_runs(ws3)["pulse"] == 1 and
              all(R.receipt_task_id(e) != "list" for e in [ev2]))

    print("== load_open_commitments memoization (R1 perf quick win)")
    with tempfile.TemporaryDirectory(prefix="receipts_memo_") as td:
        from cru_match import load_open_commitments, _OPEN_COMMITMENTS_CACHE
        from event_gate import append_event
        ws4 = Path(td) / "ws4"
        (ws4 / "_hq" / "data").mkdir(parents=True)
        ep = ws4 / "_hq" / "data" / "events.jsonl"
        append_event(ep, {"type": "commitment", "source_skill": "meeting-notes",
                          "data": {"id": "cmt_m1", "title": "t", "kind": "promise",
                                   "status": "open"}})
        first = load_open_commitments(str(ep))
        check("projection returns the open item", len(first) == 1)
        cache_entries = len(_OPEN_COMMITMENTS_CACHE)
        second = load_open_commitments(str(ep))
        check("repeat call within one fire is a cache hit (same content)",
              [e["data"]["id"] for e in second] == ["cmt_m1"]
              and len(_OPEN_COMMITMENTS_CACHE) == cache_entries)
        check("cache returns a fresh list (caller can't corrupt the cache list)",
              first is not second)
        # A closure APPEND must invalidate — the close-then-reload flow.
        append_event(ep, {"type": "commitment_resolved", "source_skill": "apply-choices",
                          "data": {"commitment_id": "cmt_m1", "resolution": "done",
                                   "resolved_by": "user", "evidence": "test"}})
        third = load_open_commitments(str(ep))
        check("append invalidates the cache (closure visible immediately)",
              len(third) == 0, repr(third))

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print(f"receipts battery: ALL PASS ({PASS} checks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
