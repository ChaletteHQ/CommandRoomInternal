#!/usr/bin/env python3
"""Unit test for value_receipt (SPEC C1 — ROI receipts).

Asserts the value-receipt compute core + the audit-event enforcement:
  - each D4 metric counts exactly (captured commitments by source_skill,
    resolved-on-time vs late via due, drafts/sent, briefs deduped, meetings,
    dormant resurfaced, decisions, documents);
  - alias commitment shapes (due_date / state) are counted (regression vs the
    Sam 2026-05-17 flat-shape bug in cru_match);
  - hours_estimate == sum(count x rubric)/60 to 2 dp, and every counted activity
    has a rubric key;
  - compute_value_receipt appends exactly ONE value_receipt_generated event with
    numbers byte-equal to the receipt; validate_receipt_ran ok=False before /
    ok=True after; a verbatim re-run inside the idempotency guard (v4.5.2 R4 /
    F-36) SKIPS the second write and notes the skip; changed numbers or a
    different rollup still write; prior events are never mutated;
  - quarterly per-month rows sum to the totals;
  - half-open window edges (start included, end excluded); empty window -> all
    zeros, honest summary, event still written;
  - malformed events.jsonl lines are skipped without crashing;
  - make_brief(brief_kind="value_receipt") writes a .docx that passes the leak
    scanner (privacy gate).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import value_receipt as vr  # noqa: E402
from value_receipt import (  # noqa: E402
    CONSERVATIVE_MINUTES_PER_UNIT,
    compute_metrics,
    compute_value_receipt,
    validate_receipt_ran,
    build_receipt_tiles,
)

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


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

def _commitment(seq, cid, *, source_skill, due=None, due_alias=False, ts):
    data = {"id": cid, "owner_id": "person_user", "title": f"commitment {cid}", "status": "open"}
    if due is not None:
        if due_alias:
            data["due_date"] = due  # alias shape (cr-past-meetings variant)
        else:
            data["due"] = due
    return {"seq": seq, "ts": ts, "type": "commitment", "source_skill": source_skill, "data": data}


def _resolved(seq, cid, *, ts):
    return {"seq": seq, "ts": ts, "type": "commitment_resolved",
            "source_skill": "reconcile-sent", "data": {"commitment_id": cid, "resolved_by": "sent_reconcile"}}


def _ev(seq, etype, *, ts, source_skill="x", data=None):
    return {"seq": seq, "ts": ts, "type": etype, "source_skill": source_skill, "data": data or {}}


def _may_fixture():
    """A single-calendar-month (May 2026) fixture with one of every counted shape."""
    return [
        # 3 captured commitments (one via each tracked skill) + 1 user-created (NOT counted).
        _commitment(1, "c1", source_skill="meeting-notes", due="2026-05-20T00:00:00", ts="2026-05-02T09:00:00"),
        _commitment(2, "c2", source_skill="inbox-triage", due="2026-05-10T00:00:00", ts="2026-05-03T09:00:00"),
        _commitment(3, "c3", source_skill="scan-for-commitments", due="2026-05-25T00:00:00", due_alias=True, ts="2026-05-04T09:00:00"),
        _commitment(4, "c4", source_skill="workspace-manager", due="2026-05-25T00:00:00", ts="2026-05-05T09:00:00"),  # user-created, not captured
        # Resolutions: c1 on time, c2 LATE, c3 (alias-due) on time.
        _resolved(10, "c1", ts="2026-05-18T09:00:00"),   # <= due 05-20 -> on time
        _resolved(11, "c2", ts="2026-05-15T09:00:00"),   # > due 05-10 -> late
        _resolved(12, "c3", ts="2026-05-21T09:00:00"),   # <= due 05-25 (via due_date alias) -> on time
        # Drafts + sends.
        _ev(20, "email_drafted", ts="2026-05-06T09:00:00"),
        _ev(21, "email_drafted", ts="2026-05-07T09:00:00"),
        _ev(22, "email_sent", ts="2026-05-08T09:00:00"),
        # Briefs: 2 morning-brief pack_runs (distinct days) + 1 same-day briefing (deduped)
        # + 1 standalone briefing on a day with no pack_run (counts).
        _ev(30, "pack_run", ts="2026-05-09T07:00:00", data={"task_id": "morning-brief"}),
        _ev(31, "pack_run", ts="2026-05-12T07:00:00", data={"task_id": "morning-brief"}),
        _ev(32, "briefing", ts="2026-05-09T07:05:00"),   # same day as a pack_run -> deduped
        _ev(33, "briefing", ts="2026-05-13T07:05:00"),   # no pack_run that day -> counts
        # 2 prep briefs.
        _ev(34, "pack_run", ts="2026-05-09T06:30:00", data={"task_id": "upcoming-meetings"}),
        _ev(35, "pack_run", ts="2026-05-10T06:30:00", data={"task_id": "upcoming-meetings"}),
        # Meetings, decisions, dormant, documents.
        _ev(40, "meeting_processed", ts="2026-05-11T09:00:00"),
        _ev(41, "meeting_processed", ts="2026-05-14T09:00:00"),
        _ev(50, "decision", ts="2026-05-15T09:00:00", source_skill="decision-log"),
        _ev(51, "decision_memo_drafted", ts="2026-05-16T09:00:00"),
        _ev(60, "pattern_break_detected", ts="2026-05-17T09:00:00"),
        _ev(61, "thread_resurrected", ts="2026-05-18T09:00:00"),
        _ev(70, "memo_drafted", ts="2026-05-19T09:00:00"),
        _ev(71, "board_pack_assembled", ts="2026-05-20T09:00:00"),
        # Out-of-window noise (April + June) — must never count.
        _ev(80, "meeting_processed", ts="2026-04-30T23:59:59"),
        _ev(81, "email_drafted", ts="2026-06-01T00:00:00"),
    ]


MAY_START = "2026-05-01T00:00:00"
MAY_END = "2026-06-01T00:00:00"


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_metric_counts():
    print("\n[1] each metric counts exactly (D4 set)")
    out = compute_metrics(_may_fixture(), MAY_START, MAY_END)
    m = out["metrics"]
    check("commitments_captured == 3 (user-created excluded)", m["commitments_captured"] == 3, m)
    check("resolved_on_time == 2 (c1 + alias-due c3; c2 late)", m["resolved_on_time"] == 2, m)
    check("drafts_produced == 2", m["drafts_produced"] == 2, m)
    check("drafts_sent == 1", m["drafts_sent"] == 1, m)
    check("morning_briefings == 3 (2 pack_runs + 1 standalone briefing, same-day deduped)",
          m["morning_briefings"] == 3, m)
    check("prep_briefs == 2", m["prep_briefs"] == 2, m)
    check("briefs_delivered == 5", m["briefs_delivered"] == 5, m)
    check("meetings_processed == 2", m["meetings_processed"] == 2, m)
    check("decisions_logged == 2 (decision + decision_memo_drafted)", m["decisions_logged"] == 2, m)
    check("dormant_resurfaced == 2", m["dormant_resurfaced"] == 2, m)
    check("documents_produced == 2", m["documents_produced"] == 2, m)


def test_alias_due_shape():
    print("\n[2] commitment due_date alias shape counts as on-time (regression vs flat-shape bug)")
    events = [
        _commitment(1, "c1", source_skill="meeting-notes", due="2026-05-20T00:00:00", due_alias=True, ts="2026-05-01T09:00:00"),
        _resolved(2, "c1", ts="2026-05-19T09:00:00"),
    ]
    m = compute_metrics(events, MAY_START, MAY_END)["metrics"]
    check("due_date alias resolved on time counted", m["resolved_on_time"] == 1, m)


def test_hours_estimate():
    print("\n[3] hours_estimate == sum(count x rubric)/60 to 2dp; rubric covers every counted activity")
    out = compute_metrics(_may_fixture(), MAY_START, MAY_END)
    m = out["metrics"]
    expected_minutes = (
        m["commitments_captured"] * CONSERVATIVE_MINUTES_PER_UNIT["commitment_captured"]
        + m["meetings_processed"] * CONSERVATIVE_MINUTES_PER_UNIT["meeting_processed"]
        + m["morning_briefings"] * CONSERVATIVE_MINUTES_PER_UNIT["morning_briefing"]
        + m["prep_briefs"] * CONSERVATIVE_MINUTES_PER_UNIT["prep_brief"]
        + m["drafts_produced"] * CONSERVATIVE_MINUTES_PER_UNIT["email_drafted"]
        + m["decisions_logged"] * CONSERVATIVE_MINUTES_PER_UNIT["decision_logged"]
        + m["dormant_resurfaced"] * CONSERVATIVE_MINUTES_PER_UNIT["cold_relationship_flag"]
    )
    check("hours_estimate matches hand-computed rubric",
          out["hours_estimate"] == round(expected_minutes / 60.0, 2),
          f"{out['hours_estimate']} vs {round(expected_minutes/60.0,2)}")
    for key in ("commitment_captured", "meeting_processed", "morning_briefing", "prep_brief",
                "email_drafted", "decision_logged", "cold_relationship_flag"):
        check(f"rubric has key {key}", key in CONSERVATIVE_MINUTES_PER_UNIT)


def test_window_edges():
    print("\n[4] half-open window: start included, end excluded")
    events = [
        _ev(1, "meeting_processed", ts=MAY_START),   # exactly at start -> included
        _ev(2, "meeting_processed", ts=MAY_END),     # exactly at end -> excluded
    ]
    m = compute_metrics(events, MAY_START, MAY_END)["metrics"]
    check("event at window_start included; at window_end excluded", m["meetings_processed"] == 1, m)


def test_empty_window():
    print("\n[5] empty window -> all zeros, honest summary, event still written")
    root = _build_ws([])
    receipt = compute_value_receipt(root, MAY_START, MAY_END)
    check("all metrics zero", all(v == 0 for v in receipt["metrics"].values()), receipt["metrics"])
    check("hours_estimate == 0", receipt["hours_estimate"] == 0, receipt)
    check("summary is honest about empty window", "no recorded activity" in receipt["summary"].lower(),
          receipt["summary"])
    rows = _read_events(root)
    vrg = [r for r in rows if r.get("type") == "value_receipt_generated"]
    check("event still written on empty window", len(vrg) == 1, vrg)
    shutil.rmtree(root, ignore_errors=True)


def test_malformed_lines():
    print("\n[6] malformed events.jsonl lines skipped, no crash")
    root = _build_ws(_may_fixture(), extra_raw_lines=["{ this is not json", "\"a bare string\"", ""])
    receipt = compute_value_receipt(root, MAY_START, MAY_END)
    check("computed despite junk lines", receipt["metrics"]["commitments_captured"] == 3, receipt["metrics"])
    check("skipped_lines reported (>=2 junk lines)", receipt["skipped_lines"] >= 2, receipt["skipped_lines"])
    shutil.rmtree(root, ignore_errors=True)


def test_audit_event_and_validator():
    print("\n[7] exactly one value_receipt_generated event, byte-equal numbers; validator before/after")
    root = _build_ws(_may_fixture())

    v0 = validate_receipt_ran(root)
    check("before any run -> validator ok=False (can't fake 'ran')", v0["ok"] is False, v0)

    receipt = compute_value_receipt(root, MAY_START, MAY_END)
    rows = _read_events(root)
    vrg = [r for r in rows if r.get("type") == "value_receipt_generated"]
    check("exactly one value_receipt_generated event", len(vrg) == 1, len(vrg))
    d = vrg[0].get("data") or {}
    check("event metrics byte-equal to receipt", d.get("metrics") == receipt["metrics"], d.get("metrics"))
    check("event hours_estimate byte-equal", d.get("hours_estimate") == receipt["hours_estimate"], d)
    check("event window matches receipt window", d.get("window") == receipt["window"], d)

    v1 = validate_receipt_ran(root, window=receipt["window"])
    check("after a real run -> validator ok=True", v1["ok"] is True, v1)
    check("validator reports the hours + metrics", v1["hours_estimate"] == receipt["hours_estimate"], v1)

    v2 = validate_receipt_ran(root, window="1999-01-01T00:00:00..1999-02-01T00:00:00")
    check("wrong-window validation -> ok=False", v2["ok"] is False, v2)
    shutil.rmtree(root, ignore_errors=True)


def test_second_run_idempotency_guard():
    print("\n[8] v4.5.2 R4 / F-36 idempotency guard: verbatim re-run inside the "
          "guard interval skips the write; changed numbers still write")
    root = _build_ws(_may_fixture())
    r1 = compute_value_receipt(root, MAY_START, MAY_END)
    check("first run writes (guard note says not skipped)",
          r1["duplicate_guard"] == {"skipped": False}, r1.get("duplicate_guard"))
    rows_before = _read_events(root)
    first_vrg = [r for r in rows_before if r.get("type") == "value_receipt_generated"][0]

    # The F-36 repro: the same window+rollup re-emitted seconds later with
    # byte-identical numbers (reproduced Jul 1 / Jul 7 / Jul 8). The guard
    # must skip the second append and note the skip on the receipt.
    r2 = compute_value_receipt(root, MAY_START, MAY_END)
    rows_after = _read_events(root)
    vrg = [r for r in rows_after if r.get("type") == "value_receipt_generated"]
    check("verbatim duplicate within guard -> still ONE event", len(vrg) == 1, len(vrg))
    check("skip noted on the returned receipt",
          r2["duplicate_guard"].get("skipped") is True, r2.get("duplicate_guard"))
    check("skip note carries the prior receipt's ts",
          r2["duplicate_guard"].get("prior_receipt_ts") == first_vrg.get("ts"),
          r2.get("duplicate_guard"))
    check("skipped run still returns the full receipt (numbers identical)",
          r2["metrics"] == r1["metrics"] and r2["hours_estimate"] == r1["hours_estimate"], r2)
    v = validate_receipt_ran(root, window=r1["window"])
    check("validator still ok for the window after a skipped duplicate", v["ok"] is True, v)
    # The original event line is untouched (same seq + same data).
    still_there = [r for r in rows_after if r.get("type") == "value_receipt_generated"
                   and r.get("seq") == first_vrg.get("seq")]
    check("the first event is unchanged (append-only, point-in-time)",
          still_there and still_there[0]["data"] == first_vrg["data"], still_there)

    # A different rollup is NOT a duplicate (the real Jul 1/7 fires wrote
    # month + quarter pairs — the pair is legitimate; only the re-emit isn't).
    rq = compute_value_receipt(root, MAY_START, MAY_END, rollup="quarter")
    check("different rollup writes its own event",
          rq["duplicate_guard"] == {"skipped": False}, rq.get("duplicate_guard"))
    rq2 = compute_value_receipt(root, MAY_START, MAY_END, rollup="quarter")
    check("quarter re-emit skipped too (the F-36 pair pattern)",
          rq2["duplicate_guard"].get("skipped") is True, rq2.get("duplicate_guard"))
    vrg = [r for r in _read_events(root) if r.get("type") == "value_receipt_generated"]
    check("two events total (month + quarter), not four", len(vrg) == 2, len(vrg))

    # New substrate activity inside the window changes the numbers -> a re-run
    # is a genuinely new snapshot and MUST write.
    from atomic_write import atomic_append_jsonl
    atomic_append_jsonl(
        os.path.join(root, "_hq", "data", "events.jsonl"),
        [_ev(999, "meeting_processed", ts="2026-05-20T09:00:00")],
    )
    r3 = compute_value_receipt(root, MAY_START, MAY_END)
    check("changed numbers within guard interval still write",
          r3["duplicate_guard"] == {"skipped": False}, r3.get("duplicate_guard"))
    check("changed-numbers receipt reflects the new event",
          r3["metrics"]["meetings_processed"] == r1["metrics"]["meetings_processed"] + 1,
          r3["metrics"])
    vrg = [r for r in _read_events(root) if r.get("type") == "value_receipt_generated"]
    check("three events after the genuine new snapshot", len(vrg) == 3, len(vrg))
    shutil.rmtree(root, ignore_errors=True)


def test_quarterly_rollup():
    print("\n[9] quarterly per-month rows sum to the totals")
    events = [
        _ev(1, "meeting_processed", ts="2026-04-10T09:00:00"),
        _ev(2, "meeting_processed", ts="2026-05-10T09:00:00"),
        _ev(3, "meeting_processed", ts="2026-06-10T09:00:00"),
        _ev(4, "email_drafted", ts="2026-04-11T09:00:00"),
        _ev(5, "email_drafted", ts="2026-06-12T09:00:00"),
        _commitment(6, "c1", source_skill="meeting-notes", ts="2026-05-15T09:00:00"),
    ]
    out = compute_metrics(events, "2026-04-01T00:00:00", "2026-07-01T00:00:00")
    check("per_month has 3 rows", len(out["per_month"]) == 3, out["per_month"])
    for metric_key in ("meetings_processed", "drafts_produced", "commitments_captured"):
        row_sum = sum(r[metric_key] for r in out["per_month"])
        check(f"per_month sums to total for {metric_key}",
              row_sum == out["metrics"][metric_key],
              f"{row_sum} vs {out['metrics'][metric_key]}")
    labels = [r["label"] for r in out["per_month"]]
    check("month labels are readable", labels == ["April 2026", "May 2026", "June 2026"], labels)


def test_brief_docx_passes_leak_scan():
    print("\n[10] make_brief(brief_kind='value_receipt') writes a leak-clean .docx (privacy gate)")
    try:
        from brief_writer import make_brief
        from docx_leak_scanner import collect_docx_leaks
    except Exception as e:  # pragma: no cover
        check("brief_writer / leak scanner import", False, f"import failed: {e}")
        return
    root = _build_ws(_may_fixture())
    receipt = compute_value_receipt(root, MAY_START, MAY_END, rollup="quarter")
    tmp = tempfile.mkdtemp(prefix="cr-vr-docx-")
    out_path = os.path.join(tmp, "Value_Receipt_2026-05.docx")
    make_brief(
        out_path,
        brief_kind="value_receipt",
        title="Value Receipt — May 2026",
        subtitle="Your operating layer, in numbers",
        sections=receipt["sections"],
    )
    check("value_receipt .docx written", os.path.isfile(out_path))
    leaks = collect_docx_leaks(out_path)
    check("docx passes leak scanner (counts + hours only, zero leaks)", leaks == [], leaks)
    shutil.rmtree(root, ignore_errors=True)
    shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# Workspace helpers
# --------------------------------------------------------------------------

def _build_ws(events, extra_raw_lines=None):
    root = tempfile.mkdtemp(prefix="cr-vr-")
    data = os.path.join(root, "_hq", "data")
    os.makedirs(data)
    with open(os.path.join(data, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
        for raw in (extra_raw_lines or []):
            f.write(raw + "\n")
    return root


def _read_events(root):
    path = os.path.join(root, "_hq", "data", "events.jsonl")
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def test_receipt_tiles():
    print("\n[11] SPEC OUT1 tile band — substrate-derived, drop-empty, above the counts")
    # Full window -> all three tiles present, values from the computed receipt.
    out = compute_metrics(_may_fixture(), MAY_START, MAY_END)
    metrics, hours = out["metrics"], out["hours_estimate"]
    tiles = build_receipt_tiles(metrics, hours)
    labels = [t["label"] for t in tiles]
    check("tiles: 'Actions handled' present", "Actions handled" in labels)
    check("tiles: 'Hours returned (conservative)' present",
          "Hours returned (conservative)" in labels)
    # Actions-handled value = the sum of the handled-count metrics (no double count
    # of the briefs sub-splits).
    from value_receipt import _ACTIONS_HANDLED_KEYS
    expected_actions = sum(int(metrics[k]) for k in _ACTIONS_HANDLED_KEYS)
    actions_tile = next(t for t in tiles if t["label"] == "Actions handled")
    check("tiles: actions value == summed handled metrics",
          actions_tile["value"] == str(expected_actions), actions_tile["value"])
    hours_tile = next(t for t in tiles if t["label"].startswith("Hours"))
    check("tiles: hours value matches receipt hours (not re-derived)",
          hours_tile["value"] == f"~{hours:g}", hours_tile["value"])
    # Empty window -> no band at all (omit-don't-pad, like _count_bullets).
    check("tiles: empty metrics -> [] (band omitted)",
          build_receipt_tiles(
              {k: 0 for k in metrics}, 0.0) == [])
    # A zero tile is dropped; a non-zero one renders (per-tile drop).
    partial = build_receipt_tiles(
        {**{k: 0 for k in metrics}, "meetings_processed": 3, "briefs_delivered": 3}, 0.0)
    plabels = [t["label"] for t in partial]
    check("tiles: zero-hours tile dropped, actions tile kept",
          plabels == ["Actions handled"], plabels)
    # The band is the FIRST section when present (above the counts).
    receipt = compute_value_receipt(_build_ws(_may_fixture()), MAY_START, MAY_END)
    check("tiles: band is the first section, above the counts",
          receipt["sections"][0].get("tiles") is not None
          and receipt["sections"][0]["heading"] == "At a glance")


def main():
    print("=== value_receipt (SPEC C1) ===")
    test_metric_counts()
    test_alias_due_shape()
    test_hours_estimate()
    test_window_edges()
    test_empty_window()
    test_malformed_lines()
    test_audit_event_and_validator()
    test_second_run_idempotency_guard()
    test_quarterly_rollup()
    test_brief_docx_passes_leak_scan()
    test_receipt_tiles()
    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
