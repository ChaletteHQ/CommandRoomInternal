#!/usr/bin/env python3
"""
Runtime exercise pass for v3.13.8 — the slow-pass methodology per
feedback_static_analysis_vs_runtime_exercise memory.

This is NOT a unit test (those exist already). This is a synthetic
end-to-end exercise that actually invokes each new code path against
realistic data, simulating the customer flows v3.13.8 changed.

Run from the command-room repo root:
    python tests/runtime_exercise_v3_13_8.py

Exits 0 on full green, 1 on any failure.
"""

from __future__ import annotations

import datetime
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))


PASS = []
FAIL = []


def _ok(name: str, detail: str = "") -> None:
    line = f"  PASS  {name}"
    if detail:
        line += f"  ({detail})"
    print(line)
    PASS.append(name)


def _fail(name: str, reason: str) -> None:
    print(f"  FAIL  {name}: {reason}")
    FAIL.append((name, reason))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


# -----------------------------------------------------------------------------
# Exercise 1: next_seq against a realistic events.jsonl with mixed shapes
# -----------------------------------------------------------------------------

def exercise_next_seq() -> None:
    _section("§2.8 next_seq.py — realistic events.jsonl with nano-epoch artifacts")
    from next_seq import next_seq, EPOCH_THRESHOLD

    tmp = Path(tempfile.mkdtemp(prefix="rt_next_seq_"))
    events = tmp / "events.jsonl"

    # Mirror M's actual events.jsonl shape per the plan §2.8: human-counter seqs
    # 1..1500 plus a contaminated tail of nano-epoch seqs from the 2026-04 bug.
    lines = []
    for i in range(1, 1501):
        lines.append(json.dumps({"seq": i, "type": "interaction"}))
    # Nano-epoch contamination (~1.7e18)
    lines.append(json.dumps({"seq": 1779426060120467301, "type": "artifact"}))
    lines.append(json.dumps({"seq": 1779426060120467302, "type": "artifact"}))
    # Plus a non-dict line and a malformed line
    lines.append('"top-level-string"')
    lines.append("not valid {")
    # Tail line with NO seq field
    lines.append(json.dumps({"type": "note", "data": "no seq"}))
    events.write_text("\n".join(lines) + "\n", encoding="utf-8")

    got = next_seq(events)
    if got == 1501:
        _ok("next_seq ignores nano-epoch + tolerates malformed + non-dict + missing-seq tail",
            f"returned {got} against a 1503-line file")
    else:
        _fail("next_seq", f"expected 1501, got {got}")

    # Empty file
    empty = tmp / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    if next_seq(empty) == 1:
        _ok("next_seq empty-file returns 1")
    else:
        _fail("next_seq empty", "should return 1")

    # Nonexistent
    missing = tmp / "does_not_exist.jsonl"
    if next_seq(missing) == 1:
        _ok("next_seq nonexistent returns 1")
    else:
        _fail("next_seq nonexistent", "should return 1")


# -----------------------------------------------------------------------------
# Exercise 2: cru_match.load_events_defensively — the actual Sub-bug #14b crash
# -----------------------------------------------------------------------------

def exercise_defensive_reader() -> None:
    _section("§2.7 Layer 2 cru_match.load_events_defensively — the AttributeError crash class")
    from cru_match import load_events_defensively, load_open_commitments

    tmp = Path(tempfile.mkdtemp(prefix="rt_defensive_"))
    events_path = tmp / "events.jsonl"

    # The actual Sub-bug #14b crash shape: line is a JSON top-level string
    contents = [
        json.dumps({"seq": 1, "type": "commitment",
                    "data": {"status": "open", "commitment_id": "c1", "title": "task A"}}),
        '"top-level-string-line"',                 # non-dict — pre-v3.13.8 crashed here
        json.dumps([1, 2, 3]),                     # non-dict list — also crashed pre-v3.13.8
        "not even json {",                         # JSONDecodeError
        '{"unterminated":',                        # JSONDecodeError
        json.dumps({"seq": 2, "type": "commitment",
                    "data": {"status": "open", "commitment_id": "c2", "title": "task B"}}),
    ]
    events_path.write_text("\n".join(contents) + "\n", encoding="utf-8")

    try:
        events, skipped = load_events_defensively(events_path)
    except Exception as e:
        _fail("load_events_defensively", f"raised {type(e).__name__}: {e}")
        return

    if len(events) == 2 and len(skipped) == 4:
        _ok("load_events_defensively returns (events, skipped) without crashing",
            f"events={len(events)}, skipped={len(skipped)}")
    else:
        _fail("load_events_defensively counts", f"expected (2, 4), got ({len(events)}, {len(skipped)})")

    # End-to-end: load_open_commitments must also survive
    try:
        open_evs = load_open_commitments(events_path)
        if len(open_evs) == 2:
            _ok("load_open_commitments surface survives malformed mid-file",
                f"returned {len(open_evs)} open commitments")
        else:
            _fail("load_open_commitments count", f"expected 2, got {len(open_evs)}")
    except Exception as e:
        _fail("load_open_commitments", f"raised {type(e).__name__}: {e}")


# -----------------------------------------------------------------------------
# Exercise 3: recover_corruption end-to-end + idempotency
# -----------------------------------------------------------------------------

def exercise_corruption_recovery() -> None:
    _section("§2.7 Layer 1 recover_corruption.run_recovery_if_needed end-to-end")
    from recover_corruption import run_recovery_if_needed, RECOVERY_VERSION
    from cru_match import load_events_defensively

    tmp = Path(tempfile.mkdtemp(prefix="rt_recovery_"))
    (tmp / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    events_path = tmp / "_hq" / "data" / "events.jsonl"

    lines = []
    for i in range(1, 51):
        lines.append(json.dumps({"seq": i, "type": "interaction"}))
    # Inject a corrupt cluster mid-file
    lines.append("{malformed-cluster-1")
    lines.append('"another-malformed-2"')
    lines.append("garbage line 3")
    for i in range(54, 101):
        lines.append(json.dumps({"seq": i, "type": "interaction"}))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # First pass — should run
    summary = run_recovery_if_needed(tmp)
    if not summary.get("ran"):
        _fail("recovery first-pass", f"expected ran=True, got {summary}")
        return
    _ok("recovery first-pass quarantines + writes corruption_recovery event",
        f"quarantined {summary['quarantined_line_count']} lines, version={summary['recovery_version']}")

    # Quarantine file should exist + be non-empty
    qf = Path(summary["quarantine_file"])
    if qf.exists() and qf.stat().st_size > 0:
        _ok("quarantine file written", f"{qf.name} = {qf.stat().st_size} bytes")
    else:
        _fail("quarantine file", f"missing or empty: {qf}")

    # events.jsonl now loads cleanly
    events, skipped = load_events_defensively(events_path)
    if len(skipped) == 0:
        _ok("post-recovery events.jsonl loads cleanly", f"events={len(events)}, skipped=0")
    else:
        _fail("post-recovery cleanliness", f"still {len(skipped)} malformed lines")

    # corruption_recovery event present in events
    recovery_evs = [e for e in events if e.get("type") == "corruption_recovery"]
    if (len(recovery_evs) == 1
        and (recovery_evs[0].get("data") or {}).get("recovery_version") == RECOVERY_VERSION):
        _ok("corruption_recovery event emitted with correct version")
    else:
        _fail("corruption_recovery event", f"unexpected: {recovery_evs}")

    # Second pass — must be a no-op (idempotency)
    second = run_recovery_if_needed(tmp)
    if not second.get("ran") and second.get("skipped_reason") == "already_run":
        _ok("recovery idempotent — second pass skipped_reason=already_run")
    else:
        _fail("recovery idempotency", f"second pass should no-op; got {second}")


# -----------------------------------------------------------------------------
# Exercise 4: atomic_write.multi_write_context concurrent-ish stress
# -----------------------------------------------------------------------------

def exercise_multi_write_context() -> None:
    _section("§2.14 atomic_write.multi_write_context — multi-write transaction + stale-lock reclaim")
    from atomic_write import (
        multi_write_context, atomic_write_json,
        release_write_lock, AtomicWriteLockError,
    )

    tmp = Path(tempfile.mkdtemp(prefix="rt_multi_write_"))

    # Simulate update-bridge's actual migration phase: 5 sequential writes inside one lock
    target_files = [tmp / "_hq" / "data" / f"file_{i}.json" for i in range(5)]
    with multi_write_context(tmp, holder="update-bridge"):
        for i, fp in enumerate(target_files):
            atomic_write_json(fp, {"step": i, "ts": str(datetime.datetime.utcnow())})

    all_present = all(fp.exists() for fp in target_files)
    lock_released = not (tmp / "_hq" / ".system" / "atomic.lock").exists()
    if all_present and lock_released:
        _ok("multi_write_context wraps 5 sequential writes; lock released on exit")
    else:
        _fail("multi_write_context basic",
              f"all_present={all_present}, lock_released={lock_released}")

    # Plant a dead-pid lock + verify reclaim
    lock_path = tmp / "_hq" / ".system" / "atomic.lock"
    lock_path.write_text(json.dumps({
        "pid": 999999999,
        "holder": "ghost",
        "acquired_at": "2026-01-01T00:00:00",
    }), encoding="utf-8")
    try:
        with multi_write_context(tmp, holder="reclaimer", timeout_s=5.0):
            payload = json.loads(lock_path.read_text(encoding="utf-8"))
            if payload.get("holder") == "reclaimer" and payload.get("pid") == os.getpid():
                _ok("dead-pid lock auto-reclaimed (Bug #18 pid-liveness)")
            else:
                _fail("dead-pid reclaim", f"unexpected payload: {payload}")
    except AtomicWriteLockError as e:
        _fail("dead-pid reclaim", f"lock should have been reclaimed: {e}")


# -----------------------------------------------------------------------------
# Exercise 5: brief_writer.make_brief with tables + matrix + leak scan
# -----------------------------------------------------------------------------

def exercise_brief_writer_e2e() -> None:
    _section("§2.2 + §2.4 brief_writer with tables/matrix + leak-scanner gate")
    from brief_writer import make_brief
    from docx_leak_scanner import LeakScanError

    tmp = Path(tempfile.mkdtemp(prefix="rt_brief_writer_"))

    # Realistic decision memo with comparison matrix
    decision = tmp / "decision.docx"
    make_brief(
        str(decision),
        brief_kind="decision_memo",
        title="Whether to hire a Head of Sales now",
        subtitle="Decision required by 2026-06-15",
        sections=[
            # EXEC1 element 2: Recommendation is above Comparison (decision-forward).
            {"heading": "Framing", "body": "Inbound is growing 30%/mo. Founder sales is at capacity. 3 conversations stuck >30d."},
            {"heading": "Recommendation",
             "body": "Hire now. Pipeline coverage outweighs runway risk at current burn."},
            {"heading": "Options", "bullets": ["A. Hire now (start Aug 1)", "B. Hire Q4 (start Jan 1)", "C. Defer 12 months"]},
            {"heading": "Criteria",
             "table": {"headers": ["Criterion", "Weight", "Why it matters"],
                       "rows": [
                           ["Pipeline coverage", "30%", "Stuck conversations have a deadline"],
                           ["Cash runway", "25%", "Hire burns $250k/yr fully loaded"],
                           ["Founder bandwidth", "25%", "Each hour on sales is an hour off product"],
                           ["Quality of hire", "20%", "Wrong hire is 6mo of damage + replacement cost"],
                       ]}},
            {"heading": "Comparison",
             "matrix": {
                 "headers_row": ["Hire now", "Hire Q4", "Defer 12mo"],
                 "headers_col": ["Pipeline", "Runway", "Bandwidth", "Hire quality"],
                 "cells": [
                     ["★★★★", "★★★", "★★"],
                     ["★★", "★★★", "★★★★"],
                     ["★★★★", "★★★", "★★"],
                     ["★★★", "★★★★", "★★★★★"],
                 ],
                 "star_col_idx": 0,
             }},
        ],
        # B3: this exercise validates table/matrix RENDERING + the leak gate,
        # not the output contract (the "Criteria" heading intentionally differs
        # from the contract's "Criteria & weights"). Opt out of the contract
        # gate so it doesn't preempt what this test verifies.
        contract="off",
    )

    if decision.exists() and decision.stat().st_size > 5000:
        _ok("decision-memo brief writes successfully with table + matrix",
            f"{decision.stat().st_size} bytes")
    else:
        _fail("decision-memo brief", f"missing or too small: {decision}")

    # Verify XML structure
    with zipfile.ZipFile(decision) as z:
        doc_xml = z.read("word/document.xml").decode("utf-8")
    structural_checks = [
        ("<w:tbl", "tables present"),
        ("★", "star glyphs present"),
        ("DECISION MEMO", "eyebrow label present"),
        ("Heading", "Heading style references present"),
    ]
    for needle, label in structural_checks:
        if needle in doc_xml:
            _ok(f"  {label}", "found")
        else:
            _fail(f"  {label}", f"missing {needle!r} in document.xml")

    # Leak-gate fires — inject project_NNN into a section body
    leaky = tmp / "leaky.docx"
    try:
        make_brief(
            str(leaky),
            brief_kind="memo",
            title="Test memo with leak",
            subtitle="should be blocked",
            sections=[{"heading": "Body", "body": "We're shipping project_020 next week."}],
            contract="off",  # B3: isolating the leak gate, not the contract gate
        )
        _fail("leak scanner gate", "expected LeakScanError but make_brief succeeded")
    except LeakScanError as e:
        if "project_020" in str(e):
            _ok("leak scanner blocks project_NNN in brief body (Bug #57)")
        else:
            _fail("leak scanner gate", f"raised but wrong content: {e}")

    # Leak-gate fires on substrate path
    leaky2 = tmp / "leaky2.docx"
    try:
        make_brief(
            str(leaky2),
            brief_kind="memo",
            title="Substrate-path test",
            subtitle="should be blocked",
            sections=[{"heading": "Body", "body": "See events.jsonl for the full history."}],
            contract="off",  # B3: isolating the leak gate, not the contract gate
        )
        _fail("leak scanner — substrate path", "expected LeakScanError")
    except LeakScanError as e:
        if "events.jsonl" in str(e):
            _ok("leak scanner blocks events.jsonl in brief body (Bug #59)")
        else:
            _fail("leak scanner — substrate path", f"wrong content: {e}")

    # Leak-gate fires on a run-split token (Bug #54)
    leaky3 = tmp / "leaky3.docx"
    try:
        make_brief(
            str(leaky3),
            brief_kind="memo",
            title="Marketing-speak test",
            subtitle="should be blocked",
            sections=[{"heading": "Body", "body": "The ecosystem of partners is leveraging the synergy."}],
            contract="off",  # B3: isolating the leak gate, not the contract gate
        )
        _fail("leak scanner — marketing", "expected LeakScanError")
    except LeakScanError:
        _ok("leak scanner blocks marketing-speak in brief body (ecosystem/synergy/leverage)")

    # Insights kind
    ins = tmp / "insights.docx"
    make_brief(
        str(ins), brief_kind="insights",
        title="Weekly insights", subtitle="2026-05-24",
        sections=[{"heading": "Observation", "body": "Customers ask about pricing in 60% of demos."}],
    )
    with zipfile.ZipFile(ins) as z:
        if "INSIGHTS" in z.read("word/document.xml").decode("utf-8"):
            _ok("'insights' brief_kind eyebrow renders (Bug #26)")
        else:
            _fail("insights kind", "INSIGHTS eyebrow missing")

    # Stress_test kind
    st = tmp / "stress.docx"
    make_brief(
        str(st), brief_kind="stress_test",
        title="Stress test: launch plan", subtitle="2026-05-24",
        sections=[{"heading": "Safeguards", "bullets": ["Plan B for X", "Plan B for Y"]}],
    )
    with zipfile.ZipFile(st) as z:
        if "STRESS TEST" in z.read("word/document.xml").decode("utf-8"):
            _ok("'stress_test' brief_kind eyebrow renders")
        else:
            _fail("stress_test kind", "STRESS TEST eyebrow missing")


# -----------------------------------------------------------------------------
# Exercise 6: docx_leak_scanner standalone — run-split token reassembly
# -----------------------------------------------------------------------------

def exercise_leak_scanner_run_collapse() -> None:
    _section("§2.4 docx_leak_scanner — run-boundary collapsing")
    from docx_leak_scanner import scan_docx_for_leaks, LeakScanError

    tmp = Path(tempfile.mkdtemp(prefix="rt_leak_scan_"))
    docx_path = tmp / "split.docx"

    # Build a minimal .docx with `ecosystem` split across two w:r runs
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        '<w:body>'
        '<w:p><w:r><w:t>The eco</w:t></w:r><w:r><w:t>system thrives.</w:t></w:r></w:p>'
        '</w:body></w:document>'
    )
    content_types = '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    rels = '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>'
    with zipfile.ZipFile(docx_path, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)

    try:
        scan_docx_for_leaks(docx_path)
        _fail("run-split reassembly", "expected LeakScanError on split ecosystem")
    except LeakScanError as e:
        if "ecosystem" in str(e):
            _ok("ecosystem split across w:r runs reassembles + is caught (Bug #54)")
        else:
            _fail("run-split reassembly", f"wrong content: {e}")


# -----------------------------------------------------------------------------
# Exercise 7: widget_transport — file URI + canonical render pipeline
# -----------------------------------------------------------------------------

def exercise_widget_transport() -> None:
    _section("§2.1 widget_transport.render_and_persist — canonical render + file URI")
    from widget_transport import render_and_persist

    tmp = Path(tempfile.mkdtemp(prefix="rt_widget_"))

    view = {
        "header": "Inbox · 1 thread.",
        "sections": [{
            "title": "TODAY", "count": 1,
            "items": [{
                "n": 1, "icon": "✉",
                "name": "Sam Sample", "subject": "Q2 deck review",
                "metadata": [("Subject", "Q2 deck"), ("To", "sam@example.com")],
                "body_lines": ["Quick check — does the Q2 deck land Friday?"],
                "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"],
            }],
        }],
    }

    t = render_and_persist(data_view=view, wrapper="fragment",
                           persist_dir=tmp / "widgets", name_hint="inbox")
    if (t["path"].exists() and t["file_uri"].startswith("file:///")
        and "Sam" in t["html"]):
        _ok("render_and_persist writes file + returns file:// URI + canonical HTML",
            f"{t['path'].stat().st_size} bytes, uri={t['file_uri'][:50]}...")
    else:
        _fail("widget_transport basic", f"unexpected transport: {t}")

    # BOM on fragment mode
    raw = t["path"].read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        _ok("UTF-8 BOM prepended on fragment-mode persistence (Bug #40)")
    else:
        _fail("UTF-8 BOM", "missing BOM on fragment-mode")

    # Document mode — no BOM
    t2 = render_and_persist(data_view=view, wrapper="document",
                            persist_dir=tmp / "widgets-doc")
    if not t2["path"].read_bytes().startswith(b"\xef\xbb\xbf"):
        _ok("document-mode persistence does NOT add BOM (already has meta charset)")
    else:
        _fail("document mode BOM", "should not have BOM in document mode")


# -----------------------------------------------------------------------------
# Exercise 8: chat_output_renderer Gate 6 — dead-chrome fix
# -----------------------------------------------------------------------------

def exercise_dead_chrome_gate() -> None:
    _section("§2.11 dead-chrome Gate 6 — send-class requires valid email")
    from chat_output_renderer import (
        render_chat_output_widget, DataShapeError, CANONICAL_ACTIONS,
    )

    if "add email then send" in CANONICAL_ACTIONS:
        _ok("'add email then send' verb is in CANONICAL_ACTIONS")
    else:
        _fail("recovery verb", "'add email then send' missing from CANONICAL_ACTIONS")

    # Placeholder To with send action — should block
    bad_view = {
        "header": "Test",
        "sections": [{
            "title": "T", "count": 1,
            "items": [{
                "n": 1, "icon": "✉",
                "name": "Sam Sample", "subject": "Test",
                "metadata": [("Subject", "Test"), ("To", "Sam (no email)")],
                "body_lines": ["body"],
                "actions": ["1 send", "1 edit then send", "1 draft", "1 skip"],
            }],
        }],
    }
    try:
        render_chat_output_widget(bad_view, wrapper="fragment")
        _fail("Gate 6 blocking", "expected DataShapeError on placeholder email")
    except DataShapeError as e:
        if "valid email" in str(e) or "add email then send" in str(e) or "Bug #44" in str(e):
            _ok("Gate 6 blocks send-class chrome with placeholder email (Bug #44)")
        else:
            _fail("Gate 6 message", f"unexpected error message: {e}")

    # Same item with real email — passes
    good_view = json.loads(json.dumps(bad_view))
    good_view["sections"][0]["items"][0]["metadata"][1] = ("To", "sam@example.com")
    try:
        html = render_chat_output_widget(good_view, wrapper="fragment")
        if "Sam" in html:
            _ok("Gate 6 passes with valid email — same item, real To: value")
        else:
            _fail("Gate 6 valid email", f"rendered but missing expected content")
    except Exception as e:
        _fail("Gate 6 valid email", f"raised {type(e).__name__}: {e}")

    # Name <email> combo
    combo = json.loads(json.dumps(good_view))
    combo["sections"][0]["items"][0]["metadata"][1] = ("To", "Sam Sample <sam@example.com>")
    try:
        render_chat_output_widget(combo, wrapper="fragment")
        _ok("Gate 6 accepts 'Name <email@x>' combo form")
    except Exception as e:
        _fail("Gate 6 combo form", f"raised {type(e).__name__}: {e}")


# -----------------------------------------------------------------------------
# Exercise 9: log_pack_run telemetry helper
# -----------------------------------------------------------------------------

def exercise_log_pack_run() -> None:
    _section("§3.9 log_pack_run telemetry helper")
    from log_pack_run import log_pack_run
    from cru_match import load_events_defensively

    tmp = Path(tempfile.mkdtemp(prefix="rt_pack_run_"))
    ev = log_pack_run(
        workspace_root=tmp, kind="commitments",
        surfaced=7, duration_ms=2300,
        source_skill="cr-commitments", fired_via="scheduled",
    )
    if ev.get("type") == "pack_run" and (ev.get("data") or {}).get("kind") == "commitments":
        _ok("log_pack_run appends valid pack_run event")
    else:
        _fail("log_pack_run shape", f"unexpected event: {ev}")

    events, _ = load_events_defensively(tmp / "_hq" / "data" / "events.jsonl")
    if len(events) == 1 and events[0]["data"]["duration_ms"] == 2300:
        _ok("log_pack_run round-trips through events.jsonl + defensive reader")
    else:
        _fail("log_pack_run round-trip", f"unexpected events: {events}")

    # Validation guards
    failures = []
    for bad in [
        {"kind": "", "surfaced": 0, "duration_ms": 0},
        {"kind": "x", "surfaced": -1, "duration_ms": 0},
        {"kind": "x", "surfaced": 0, "duration_ms": -1},
    ]:
        try:
            log_pack_run(workspace_root=tmp, source_skill="x", **bad)
            failures.append(bad)
        except ValueError:
            pass
    if not failures:
        _ok("log_pack_run validates kind/surfaced/duration_ms")
    else:
        _fail("log_pack_run validation", f"accepted bad inputs: {failures}")


# -----------------------------------------------------------------------------
# Exercise 10: release detector v3_13_8_substrate_corruption
# -----------------------------------------------------------------------------

def exercise_release_detector() -> None:
    _section("§5 release_detectors.v3_13_8_substrate_corruption — gated by malformed lines")
    from release_detectors.v3_13_8_substrate_corruption import has_malformed_events
    from recover_corruption import run_recovery_if_needed

    tmp = Path(tempfile.mkdtemp(prefix="rt_release_det_"))
    (tmp / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    events_path = tmp / "_hq" / "data" / "events.jsonl"

    # No file → False
    r = has_malformed_events(events_path)
    if not r.get("applies"):
        _ok("detector returns applies=False when events.jsonl is missing")
    else:
        _fail("detector missing-file", f"unexpected: {r}")

    # Clean file → False
    events_path.write_text(json.dumps({"seq": 1, "type": "x"}) + "\n", encoding="utf-8")
    r = has_malformed_events(events_path)
    if not r.get("applies"):
        _ok("detector returns applies=False on clean events.jsonl")
    else:
        _fail("detector clean", f"unexpected: {r}")

    # Corrupt file → True
    events_path.write_text("\n".join([
        json.dumps({"seq": 1, "type": "x"}),
        '"top-string"',
        json.dumps({"seq": 2, "type": "x"}),
    ]) + "\n", encoding="utf-8")
    r = has_malformed_events(events_path)
    if r.get("applies") and r["context"]["count"] == 1:
        _ok("detector returns applies=True with skipped-line count when malformed lines exist")
    else:
        _fail("detector corrupt", f"unexpected: {r}")

    # After recovery → False (idempotency)
    run_recovery_if_needed(tmp)
    r = has_malformed_events(events_path)
    if not r.get("applies"):
        _ok("detector returns applies=False after recovery has run (idempotency)")
    else:
        _fail("detector idempotency", f"detector re-fires after recovery: {r}")


# -----------------------------------------------------------------------------
# Exercise 11: ENTITY_RESOLVE_PROTOCOL injection landed in 10 skills
# -----------------------------------------------------------------------------

def exercise_protocol_injection() -> None:
    _section("§2.6 ENTITY_RESOLVE_PROTOCOL injection in 10 skills")
    skills = [
        "workspace-manager", "people-crm", "transcript-search",
        "thread-resurrection", "intro-broker", "follow-up-ritual",
        "calendar-writer", "email-writer", "dormant-customer-scan",
        "morning-briefing",
    ]
    missing = []
    for s in skills:
        text = (ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
        # Either references the shared protocol or invokes the resolver canonically
        ok = ("shared/ENTITY_RESOLVE_PROTOCOL.md" in text
              or "from entity_resolve import" in text
              or "entity_resolve.py::resolve_all" in text)
        if not ok:
            missing.append(s)
    if not missing:
        _ok(f"all 10 skills carry the ENTITY_RESOLVE_PROTOCOL marker")
    else:
        _fail("protocol injection", f"missing in: {missing}")


# -----------------------------------------------------------------------------
# Exercise 12: brief_writer skills carry the brief_writer migration instruction
# -----------------------------------------------------------------------------

def exercise_brief_writer_migration_instr() -> None:
    _section("§2.3 brief_writer migration instructions in 7 .docx skills")
    skills_kinds = {
        "memo-writer": "memo",
        "one-pager-composer": "one_pager",
        "decision-memo-composer": "decision_memo",
        "board-pack-assembler": "board_pack",
        "automation-scanner": "automation_scan",
        "stress-test": "stress_test",
        "insight-generator": "insights",
    }
    missing = []
    for s, kind in skills_kinds.items():
        text = (ROOT / "skills" / s / "SKILL.md").read_text(encoding="utf-8")
        has_brief = "brief_writer" in text or "make_brief" in text
        has_kind = f'"{kind}"' in text or kind in text
        if not (has_brief and has_kind):
            missing.append(f"{s} (brief_writer={has_brief}, kind={kind!r}={has_kind})")
    if not missing:
        _ok("all 7 .docx skills reference brief_writer + their canonical brief_kind")
    else:
        _fail("brief_writer migration", f"missing in: {missing}")


# -----------------------------------------------------------------------------
# Exercise 13: end-to-end integration: defensive read + recovery + next_seq
# -----------------------------------------------------------------------------

def exercise_e2e_substrate_lifecycle() -> None:
    _section("E2E — defensive read → recovery → next_seq round-trip")
    from cru_match import load_events_defensively, load_open_commitments
    from recover_corruption import run_recovery_if_needed
    from next_seq import next_seq

    tmp = Path(tempfile.mkdtemp(prefix="rt_e2e_"))
    (tmp / "_hq" / "data").mkdir(parents=True, exist_ok=True)
    events_path = tmp / "_hq" / "data" / "events.jsonl"

    # Simulate a workspace with some commitments + some corruption + a nano-epoch artifact
    lines = []
    for i in range(1, 21):
        lines.append(json.dumps({
            "seq": i, "type": "commitment",
            "data": {"status": "open", "commitment_id": f"c{i}", "title": f"task {i}"}
        }))
    # Inject the nano-epoch artifact
    lines.append(json.dumps({"seq": 1779426060120467301, "type": "interaction"}))
    # And a corrupt block
    lines.append('"bad-string-1"')
    lines.append("{malformed-2")
    lines.append(json.dumps({"seq": 21, "type": "commitment",
                             "data": {"status": "open", "commitment_id": "c21", "title": "task 21"}}))
    events_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Step A: load_open_commitments still works (does not crash)
    open_evs = load_open_commitments(events_path)
    if len(open_evs) == 21:
        _ok("Step A — load_open_commitments returns 21 open commitments despite corruption")
    else:
        _fail("E2E Step A", f"expected 21, got {len(open_evs)}")

    # Step B: defensive reader reports the skipped lines
    _, skipped = load_events_defensively(events_path)
    if len(skipped) == 2:
        _ok("Step B — defensive reader reports 2 skipped lines (surface to user)")
    else:
        _fail("E2E Step B", f"expected 2 skipped, got {len(skipped)}: {skipped}")

    # Step C: next_seq ignores the nano-epoch + corrupt lines
    ns = next_seq(events_path)
    if ns == 22:
        _ok("Step C — next_seq returns 22 (ignores nano-epoch + corrupt + tail)")
    else:
        _fail("E2E Step C", f"expected 22, got {ns}")

    # Step D: recovery runs, quarantines the corrupt lines
    summary = run_recovery_if_needed(tmp)
    if summary["ran"] and summary["quarantined_line_count"] >= 2:
        _ok("Step D — recovery quarantines corrupt lines, writes corruption_recovery")
    else:
        _fail("E2E Step D", f"recovery unexpected: {summary}")

    # Step E: post-recovery, defensive reader is clean
    _, skipped2 = load_events_defensively(events_path)
    if len(skipped2) == 0:
        _ok("Step E — post-recovery events.jsonl is clean")
    else:
        _fail("E2E Step E", f"still {len(skipped2)} skipped")

    # Step F: next_seq still works (corruption_recovery event has seq, nano-epoch still ignored)
    ns2 = next_seq(events_path)
    if ns2 >= 22:
        _ok(f"Step F — post-recovery next_seq returns {ns2} (>= prior 22)")
    else:
        _fail("E2E Step F", f"unexpected next_seq after recovery: {ns2}")


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------

def report() -> int:
    print("\n" + "=" * 70)
    print(f"  PASSED: {len(PASS)}")
    print(f"  FAILED: {len(FAIL)}")
    if FAIL:
        print("\n  Failures:")
        for name, reason in FAIL:
            print(f"    - {name}: {reason}")
        return 1
    return 0


def main() -> int:
    print(f"v3.13.8 runtime exercise pass — slow-pass methodology")
    print(f"Repository: {ROOT}")
    exercise_next_seq()
    exercise_defensive_reader()
    exercise_corruption_recovery()
    exercise_multi_write_context()
    exercise_brief_writer_e2e()
    exercise_leak_scanner_run_collapse()
    exercise_widget_transport()
    exercise_dead_chrome_gate()
    exercise_log_pack_run()
    exercise_release_detector()
    exercise_protocol_injection()
    exercise_brief_writer_migration_instr()
    exercise_e2e_substrate_lifecycle()
    return report()


if __name__ == "__main__":
    raise SystemExit(main())
