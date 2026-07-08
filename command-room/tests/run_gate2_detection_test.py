#!/usr/bin/env python3
"""Tests for SPEC GATE2 — enforcement by detection.

Covers the load-bearing pieces:
  - the unified content scanner (docx_leak_scanner.scan_docx_for_violations) —
    catches voice tells + leaks in a HAND-ROLLED .docx (the proof that detection
    works regardless of how the file was made);
  - scan_text_for_leaks over chat prose (the memo-as-chat path);
  - deliverable_sweep: candidate discovery (excludes archive/backup, time-window),
    sweep_workspace (flag-only + read-only + emits a detectable gate_ran event +
    writes a findings record), the gate_ran join, and plain-English surfacing;
  - turn_backstop self-resolving workspace_root + catching leaks in email bodies;
  - the Stop-hook runner exits 0 and surfaces;
  - hooks/hooks.json is valid and points at the runner.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))


# ---------- helpers ----------

def _build_docx(path: Path, paragraphs: list[str]) -> None:
    """Write a minimal valid .docx (one <w:p> per paragraph) — mimics a doc the
    LLM hand-rolled with python-docx, NOT routed through brief_writer."""
    inner = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    ct = (
        '<?xml version="1.0"?><Types '
        'xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
    )
    doc = (
        '<?xml version="1.0"?><w:document '
        'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + inner + "</w:body></w:document>"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(path), "w") as z:
        z.writestr("[Content_Types].xml", ct)
        z.writestr("word/document.xml", doc)


def _fresh_ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="gate2_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    # entities.json so the canonical workspace_root resolver anchors here.
    (ws / "_hq" / "data" / "entities.json").write_text(
        '{"entities": {"threads": []}}', encoding="utf-8"
    )
    return ws


# ---------- unified scanner ----------

def test_unified_scanner_catches_hand_rolled_violations() -> None:
    from docx_leak_scanner import scan_docx_for_violations

    p = Path(tempfile.mkdtemp()) / "memo.docx"
    # Reproduces the live-test decision memo: 'leverage' x2 + a Phase-N leak +
    # a tri-colon construction. None of this went through make_brief.
    _build_docx(
        p,
        [
            "We will leverage the pipeline to leverage scale.",
            "Phase 3 kicks off next week.",
            "Status: green. Owner: Sam. Risk: low.",
        ],
    )
    r = scan_docx_for_violations(p)
    assert r["has_violation"] is True, r
    leak_matches = {f["match"] for f in r["leaks"]}
    assert "leverage" in leak_matches, leak_matches
    assert "Phase 3" in leak_matches, leak_matches
    voice_rules = {f["rule"] for f in r["voice"]["findings"]}
    assert "structural_tri_colon" in voice_rules, voice_rules
    print("PASS test_unified_scanner_catches_hand_rolled_violations")


def test_unified_scanner_catches_banned_phrase() -> None:
    from docx_leak_scanner import scan_docx_for_violations

    p = Path(tempfile.mkdtemp()) / "email.docx"
    _build_docx(p, ["I hope this email finds you well.", "Quick update on the deck."])
    r = scan_docx_for_violations(p)
    fails = [f for f in r["voice"]["findings"] if f["severity"] == "fail"]
    assert any(f["rule"] == "filler_finds_well" for f in fails), r["voice"]
    assert r["has_violation"] is True, r
    print("PASS test_unified_scanner_catches_banned_phrase")


def test_unified_scanner_clean_doc_no_violation() -> None:
    from docx_leak_scanner import scan_docx_for_violations

    p = Path(tempfile.mkdtemp()) / "clean.docx"
    _build_docx(p, ["Tuesday works.", "I will send the draft by Monday."])
    r = scan_docx_for_violations(p)
    assert r["has_violation"] is False, r
    assert r["leaks"] == [], r["leaks"]
    print("PASS test_unified_scanner_clean_doc_no_violation")


def test_unified_scanner_flags_unreadable_never_raises() -> None:
    from docx_leak_scanner import scan_docx_for_violations

    # Missing file → error flag, no crash, no false-clean.
    r = scan_docx_for_violations(Path(tempfile.mkdtemp()) / "nope.docx")
    assert "error" in r, r
    assert r["has_violation"] is False, r

    # A non-zip file with a .docx name → unreadable, flagged, not crashed.
    bad = Path(tempfile.mkdtemp()) / "bad.docx"
    bad.write_text("this is not a zip", encoding="utf-8")
    r2 = scan_docx_for_violations(bad)
    assert "error" in r2, r2
    print("PASS test_unified_scanner_flags_unreadable_never_raises")


def test_scan_text_for_leaks_chat_prose() -> None:
    from docx_leak_scanner import scan_text_for_leaks

    text = "Phase 2 starts Monday; we will leverage project_020 for the rollout."
    matches = {f["match"] for f in scan_text_for_leaks(text)}
    assert "Phase 2" in matches, matches
    assert "leverage" in matches, matches
    assert "project_020" in matches, matches
    assert scan_text_for_leaks("Tuesday works fine.") == []
    print("PASS test_scan_text_for_leaks_chat_prose")


# ---------- deliverable sweep ----------

def test_find_candidate_docx_excludes_and_windows() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    _build_docx(ws / "root_handroll.docx", ["leverage"])
    _build_docx(ws / "_hq" / "meetings" / "brief.docx", ["clean"])
    _build_docx(ws / "_archive" / "old.docx", ["leverage"])     # excluded dir
    _build_docx(ws / "_hq" / "backups" / "bk.docx", ["leverage"])  # excluded dir

    found = ds.find_candidate_docx(ws)
    names = {p.name for p in found}
    assert "root_handroll.docx" in names, names
    assert "brief.docx" in names, names
    assert "old.docx" not in names, names
    assert "bk.docx" not in names, names

    # Time window: nothing modified in the future second → empty.
    future = time.time() + 3600
    assert ds.find_candidate_docx(ws, since_ts=future) == []
    print("PASS test_find_candidate_docx_excludes_and_windows")


def test_sweep_workspace_flags_emits_and_is_readonly() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    dirty = ws / "OnePager_branded.docx"
    _build_docx(dirty, ["We will leverage scale.", "Phase 2 now."])
    _build_docx(ws / "_hq" / "meetings" / "clean.docx", ["Tuesday works."])

    before_bytes = dirty.read_bytes()
    before_mtime = dirty.stat().st_mtime

    res = ds.sweep_workspace(ws, emit=True, source="cleanup_sweep")
    assert res["scanned"] == 2, res
    assert res["violation_count"] == 1, res
    flagged_docs = {Path(f["path"]).name for f in res["flagged"]}
    assert "OnePager_branded.docx" in flagged_docs, flagged_docs
    assert "clean.docx" not in flagged_docs, flagged_docs

    # READ-ONLY: the user's doc is byte-identical and untouched.
    assert dirty.read_bytes() == before_bytes, "sweep mutated the user's file!"
    assert dirty.stat().st_mtime == before_mtime, "sweep touched the file mtime!"
    assert dirty.exists(), "sweep deleted the user's file!"

    # DETECTABLE: a gate_ran event landed in events.jsonl.
    events = [
        json.loads(l)
        for l in (ws / "_hq" / "data" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    gate_evs = [e for e in events if e.get("type") == "gate_ran"]
    assert gate_evs, "no gate_ran event emitted"
    assert gate_evs[-1]["data"]["surface"] == "cleanup_sweep", gate_evs[-1]
    assert gate_evs[-1]["data"]["result"] == "fail", gate_evs[-1]

    # DURABLE: a findings record was written under the CR system dir only.
    recs = list((ws / "_hq" / ".system" / "gate2_findings").glob("*.json"))
    assert recs, "no findings record written"
    print("PASS test_sweep_workspace_flags_emits_and_is_readonly")


def test_summarize_for_user_is_plain_english() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    _build_docx(ws / "Memo_draft.docx", ["We will leverage Phase 3 outcomes."])
    res = ds.sweep_workspace(ws, emit=False)
    summary = ds.summarize_for_user(res)
    assert summary is not None
    # Names the doc by filename, surfaces the offending word for the rewrite.
    assert "Memo_draft.docx" in summary, summary
    assert "leverage" in summary, summary
    # No internal jargon / paths in the user-facing surface (CONTRACT Rule 4).
    for forbidden in ("_hq/", "gate_ran", "voice_tell", "marketing_leverage",
                      "structural_", ".jsonl", "make_brief"):
        assert forbidden not in summary, f"leaked {forbidden!r}: {summary}"
    # Clean sweep → None.
    ws2 = _fresh_ws()
    _build_docx(ws2 / "clean.docx", ["Tuesday works."])
    assert ds.summarize_for_user(ds.sweep_workspace(ws2, emit=False)) is None
    print("PASS test_summarize_for_user_is_plain_english")


def test_detect_gate_bypass_join() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    ep = ws / "_hq" / "data" / "events.jsonl"
    # 2 deliverable events, only 1 docx gate_ran → 1 suspected bypass.
    lines = [
        {"seq": 1, "type": "memo_drafted", "data": {}},
        {"seq": 2, "type": "one_pager_drafted", "data": {}},
        {"seq": 3, "type": "gate_ran", "data": {"surface": "docx"}},
        {"seq": 4, "type": "gate_ran", "data": {"surface": "chat_email"}},  # not docx
    ]
    ep.write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    r = ds.detect_gate_bypass(ws)
    assert r["deliverables"] == 2, r
    assert r["docx_gate_ran"] == 1, r
    assert r["suspected_bypass"] == 1, r
    print("PASS test_detect_gate_bypass_join")


def test_detect_gate_bypass_tolerates_malformed() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    ep = ws / "_hq" / "data" / "events.jsonl"
    ep.write_text(
        '{"seq": 1, "type": "memo_drafted", "data": {}}\n'
        "this is a corrupt half-line\n"
        '{"seq": 2, "type": "gate_ran", "data": {"surface": "docx"}}\n',
        encoding="utf-8",
    )
    r = ds.detect_gate_bypass(ws)  # must not raise on the malformed line
    assert r["deliverables"] == 1 and r["docx_gate_ran"] == 1, r
    print("PASS test_detect_gate_bypass_tolerates_malformed")


# ---------- turn backstop rewire ----------

def test_turn_backstop_catches_leak_in_email_body() -> None:
    from turn_backstop import scan_data_view_for_tells

    data = {
        "source_skill": "email-writer",
        "sections": [
            {
                "items": [
                    {
                        "n": 1,
                        "metadata": [["To", "sam@example.com"], ["Subject", "Update"]],
                        "body_lines": [
                            "Hope this email finds you well.",  # voice fail
                            "Phase 3 is on track.",             # leak
                        ],
                    }
                ]
            }
        ],
    }
    # No workspace_root passed → must still scan (self-resolves; emit no-ops
    # cleanly when not in a workspace). Findings must include BOTH the voice tell
    # and the leak.
    r = scan_data_view_for_tells(data, emit=False)
    assert r["items_scanned"] == 1, r
    rules = {f.get("rule") for f in r["findings"]}
    assert "filler_finds_well" in rules, rules
    assert any(str(x).startswith("leak_") for x in rules), rules
    print("PASS test_turn_backstop_catches_leak_in_email_body")


def test_turn_backstop_emits_with_resolved_root() -> None:
    from turn_backstop import scan_data_view_for_tells

    ws = _fresh_ws()
    data = {
        "sections": [
            {
                "items": [
                    {
                        "n": 1,
                        "metadata": [["To", "a@example.com"], ["Subject", "Hi"]],
                        "body_lines": ["I'd be happy to help with this."],
                    }
                ]
            }
        ]
    }
    # Explicit workspace_root → a gate_ran(chat_email) event must land.
    scan_data_view_for_tells(data, workspace_root=str(ws), emit=True)
    events = [
        json.loads(l)
        for l in (ws / "_hq" / "data" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    chat_evs = [
        e
        for e in events
        if e.get("type") == "gate_ran"
        and (e.get("data") or {}).get("surface") == "chat_email"
    ]
    assert chat_evs, "turn_backstop did not emit a chat_email gate_ran event"
    assert chat_evs[-1]["data"]["result"] == "fail", chat_evs[-1]
    print("PASS test_turn_backstop_emits_with_resolved_root")


# ---------- Stop hook ----------

def test_stop_hook_runner_exits_zero_and_surfaces() -> None:
    ws = _fresh_ws()
    _build_docx(ws / "branded.docx", ["We will leverage growth."])
    # Make it recent so the 10-min turn window includes it.
    runner = ROOT / "shared" / "scripts" / "gate2_turn_sweep.py"
    proc = subprocess.run(
        [sys.executable, str(runner)],
        input=json.dumps({"cwd": str(ws), "hook_event_name": "Stop"}),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "leverage" in proc.stdout, proc.stdout
    assert "quality gate" in proc.stdout.lower(), proc.stdout
    print("PASS test_stop_hook_runner_exits_zero_and_surfaces")


def test_stop_hook_scans_chat_prose_from_transcript() -> None:
    """SPEC GATE2 D4 — the Test 2 gap: a memo drafted as chat prose (no file, no
    widget). The Stop hook scans the just-finished assistant turn from the
    transcript and flags the tell/leak."""
    ws = _fresh_ws()
    # Transcript JSONL with an assistant turn that leaked 'Phase 3' + a tell.
    tdir = Path(tempfile.mkdtemp())
    transcript = tdir / "t.jsonl"
    transcript.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"type": "user", "message": {"role": "user", "content": "write a memo"}},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "text",
                                "text": "Here is the memo. Phase 3 begins and we will leverage scale.",
                            }
                        ],
                    },
                },
            ]
        ),
        encoding="utf-8",
    )
    runner = ROOT / "shared" / "scripts" / "gate2_turn_sweep.py"
    proc = subprocess.run(
        [sys.executable, str(runner)],
        input=json.dumps(
            {"cwd": str(ws), "hook_event_name": "Stop",
             "transcript_path": str(transcript)}
        ),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    assert "Phase 3" in proc.stdout or "leverage" in proc.stdout, proc.stdout
    print("PASS test_stop_hook_scans_chat_prose_from_transcript")


def test_stop_hook_runner_never_breaks_on_garbage_input() -> None:
    runner = ROOT / "shared" / "scripts" / "gate2_turn_sweep.py"
    # Malformed stdin + no workspace → must still exit 0, no crash.
    proc = subprocess.run(
        [sys.executable, str(runner)],
        input="}{not json at all",
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    print("PASS test_stop_hook_runner_never_breaks_on_garbage_input")


def test_hooks_json_valid_and_wired() -> None:
    hj = ROOT / "hooks" / "hooks.json"
    assert hj.exists(), "hooks/hooks.json missing"
    cfg = json.loads(hj.read_text(encoding="utf-8"))
    stop_hooks = cfg["hooks"]["Stop"]
    cmds = [
        h["command"]
        for entry in stop_hooks
        for h in entry["hooks"]
        if h.get("type") == "command"
    ]
    assert any("gate2_turn_sweep.py" in c for c in cmds), cmds
    assert any("CLAUDE_PLUGIN_ROOT" in c for c in cmds), cmds
    print("PASS test_hooks_json_valid_and_wired")


def main() -> int:
    test_unified_scanner_catches_hand_rolled_violations()
    test_unified_scanner_catches_banned_phrase()
    test_unified_scanner_clean_doc_no_violation()
    test_unified_scanner_flags_unreadable_never_raises()
    test_scan_text_for_leaks_chat_prose()
    test_find_candidate_docx_excludes_and_windows()
    test_sweep_workspace_flags_emits_and_is_readonly()
    test_summarize_for_user_is_plain_english()
    test_detect_gate_bypass_join()
    test_detect_gate_bypass_tolerates_malformed()
    test_turn_backstop_catches_leak_in_email_body()
    test_turn_backstop_emits_with_resolved_root()
    test_stop_hook_runner_exits_zero_and_surfaces()
    test_stop_hook_scans_chat_prose_from_transcript()
    test_stop_hook_runner_never_breaks_on_garbage_input()
    test_hooks_json_valid_and_wired()
    print("\nALL gate2_detection tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
