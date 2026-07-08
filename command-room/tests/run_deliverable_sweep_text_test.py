#!/usr/bin/env python3
"""Tests for SPEC GATE2-v2 — markdown/text deliverable coverage + on-demand
point-at-target sweep.

The live GATE2 re-run proved the .docx-only sweep missed the real violations:
the model hand-rolled a drafted email and a decision memo as `.md` files (a
banned opener + a process-stage leak), and they shipped uncaught. GATE2-v2
extends `deliverable_sweep` to scan deliverable-shaped `.md`/`.markdown` too —
while deliberately NOT scanning context/memory markdown (session notes, the
workspace brief, build specs, views) so the sweep stays noise-free.

Covers:
  - find_candidate_text picks up deliverable .md and skips infra markdown
    (by filename stem AND by directory);
  - find_candidate_deliverables merges docx + md, newest-first;
  - scan_text_file returns the docx-result shape, catches leaks + tells,
    and never raises on a missing / oversized file;
  - scan_path_for_violations dispatches by extension;
  - sweep_workspace now FLAGS a hand-rolled .md deliverable while leaving the
    user's file byte-identical (read-only), and does NOT flag infra markdown;
  - sweep_targets (the on-demand point-at-target path) returns the same shape
    summarize_for_user consumes.
"""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))


def _fresh_ws() -> Path:
    ws = Path(tempfile.mkdtemp(prefix="gate2v2_ws_"))
    (ws / "_hq" / "data").mkdir(parents=True)
    (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
    (ws / "_hq" / "data" / "entities.json").write_text(
        '{"entities": {"threads": []}}', encoding="utf-8"
    )
    return ws


# A drafted email/memo escaped as markdown — the exact live-test failure class:
# a process-stage leak ("Phase 1") + a banned opener.
_DIRTY_MD = (
    "I hope this email finds you well.\n\n"
    "Quick update: Phase 1 wraps Friday and we will leverage the new flow.\n"
)
# Context/memory markdown that legitimately contains the same language — must
# NEVER be flagged (it is not a deliverable).
_INFRA_MD = "## Phase 1 notes\n\nWe leverage events.jsonl here; Phase 2 next.\n"


# ---------- candidate discovery ----------

def test_find_candidate_text_includes_deliverables_excludes_infra() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    # Deliverable-shaped markdown at the workspace root + a client deliverables dir.
    (ws / "client_update.md").write_text(_DIRTY_MD, encoding="utf-8")
    (ws / "Acme" / "deliverables").mkdir(parents=True)
    (ws / "Acme" / "deliverables" / "outbound_note.md").write_text(
        _DIRTY_MD, encoding="utf-8"
    )
    # Infra markdown — excluded by filename stem.
    (ws / "SESSION_NOTES_ACME.md").write_text(_INFRA_MD, encoding="utf-8")
    (ws / "CLAUDE.md").write_text(_INFRA_MD, encoding="utf-8")
    # Infra markdown — excluded by directory.
    (ws / "build-specs").mkdir()
    (ws / "build-specs" / "SPEC_X.md").write_text(_INFRA_MD, encoding="utf-8")
    (ws / "_archive").mkdir()
    (ws / "_archive" / "old_note.md").write_text(_DIRTY_MD, encoding="utf-8")

    names = {p.name for p in ds.find_candidate_text(ws)}
    assert "client_update.md" in names, names
    assert "outbound_note.md" in names, names
    assert "SESSION_NOTES_ACME.md" not in names, names
    assert "CLAUDE.md" not in names, names
    assert "SPEC_X.md" not in names, names
    assert "old_note.md" not in names, names  # excluded archive dir
    print("PASS test_find_candidate_text_includes_deliverables_excludes_infra")


def test_find_candidate_deliverables_merges_docx_and_md() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    (ws / "note.md").write_text(_DIRTY_MD, encoding="utf-8")
    # Minimal .docx (reuse the gate2 test's builder shape inline).
    import zipfile

    d = ws / "doc.docx"
    with zipfile.ZipFile(str(d), "w") as z:
        z.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/'
            'package/2006/content-types"><Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.'
            'wordprocessingml.document.main+xml"/></Types>',
        )
        z.writestr(
            "word/document.xml",
            '<?xml version="1.0"?><w:document xmlns:w="http://schemas.'
            'openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            "<w:p><w:r><w:t>Tuesday works.</w:t></w:r></w:p></w:body></w:document>",
        )
    found = {p.name for p in ds.find_candidate_deliverables(ws)}
    assert "note.md" in found and "doc.docx" in found, found
    print("PASS test_find_candidate_deliverables_merges_docx_and_md")


# ---------- text-file scanner ----------

def test_scan_text_file_catches_and_shapes() -> None:
    import deliverable_sweep as ds

    p = Path(tempfile.mkdtemp()) / "draft.md"
    p.write_text(_DIRTY_MD, encoding="utf-8")
    r = ds.scan_text_file(p)
    # Same shape as scan_docx_for_violations.
    for key in ("path", "leaks", "voice", "has_violation", "has_voice_warn"):
        assert key in r, (key, r)
    assert r["has_violation"] is True, r
    leak_matches = {x["match"] for x in r["leaks"]}
    assert "Phase 1" in leak_matches, leak_matches
    assert "leverage" in leak_matches, leak_matches
    print("PASS test_scan_text_file_catches_and_shapes")


def test_scan_text_file_clean_and_unreadable() -> None:
    import deliverable_sweep as ds

    clean = Path(tempfile.mkdtemp()) / "ok.md"
    clean.write_text("Tuesday works. I will send the draft Monday.\n", encoding="utf-8")
    assert ds.scan_text_file(clean)["has_violation"] is False

    # Missing file → error flag, never raises, never false-clean-violation.
    r = ds.scan_text_file(Path(tempfile.mkdtemp()) / "nope.md")
    assert "error" in r and r["has_violation"] is False, r
    print("PASS test_scan_text_file_clean_and_unreadable")


def test_scan_path_for_violations_dispatches() -> None:
    import deliverable_sweep as ds

    md = Path(tempfile.mkdtemp()) / "x.md"
    md.write_text(_DIRTY_MD, encoding="utf-8")
    r = ds.scan_path_for_violations(md)
    assert r["has_violation"] is True, r
    print("PASS test_scan_path_for_violations_dispatches")


# ---------- workspace sweep with md ----------

def test_sweep_workspace_flags_md_not_infra_and_readonly() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    dirty = ws / "outbound_draft.md"
    dirty.write_text(_DIRTY_MD, encoding="utf-8")
    infra = ws / "SESSION_NOTES_X.md"
    infra.write_text(_INFRA_MD, encoding="utf-8")
    clean = ws / "fine.md"
    clean.write_text("Tuesday works.\n", encoding="utf-8")

    before = dirty.read_bytes()
    res = ds.sweep_workspace(ws, emit=True, source="on_demand_sweep")

    flagged = {Path(f["path"]).name for f in res["flagged"]}
    assert "outbound_draft.md" in flagged, flagged
    assert "SESSION_NOTES_X.md" not in flagged, flagged  # infra never scanned
    assert "fine.md" not in flagged, flagged
    assert res["violation_count"] >= 1, res

    # READ-ONLY: the user's file is untouched.
    assert dirty.read_bytes() == before, "sweep mutated the .md file!"
    assert dirty.exists()

    # Plain-English summary names the file + the offending word, no jargon.
    summary = ds.summarize_for_user(res)
    assert summary and "outbound_draft.md" in summary, summary
    for forbidden in ("_hq/", "gate_ran", "voice_tell", ".jsonl", "structural_"):
        assert forbidden not in summary, f"leaked {forbidden!r}: {summary}"
    print("PASS test_sweep_workspace_flags_md_not_infra_and_readonly")


# ---------- on-demand point-at-target ----------

def test_sweep_targets_shape_and_summary() -> None:
    import deliverable_sweep as ds

    ws = _fresh_ws()
    p = ws / "one_off.md"
    p.write_text(_DIRTY_MD, encoding="utf-8")
    res = ds.sweep_targets([str(p)], workspace_root=str(ws), emit=True)
    assert res["scanned"] == 1, res
    assert res["violation_count"] == 1, res
    assert ds.summarize_for_user(res) is not None

    # emit wrote a findings record + a gate_ran event (CR-owned telemetry only).
    recs = list((ws / "_hq" / ".system" / "gate2_findings").glob("*.json"))
    assert recs, "no findings record from sweep_targets"
    events = [
        json.loads(l)
        for l in (ws / "_hq" / "data" / "events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if l.strip()
    ]
    assert any(e.get("type") == "gate_ran" for e in events), events

    # A clean target → no flags, summary None.
    c = ws / "clean_one.md"
    c.write_text("Tuesday works.\n", encoding="utf-8")
    res2 = ds.sweep_targets([str(c)], workspace_root=str(ws), emit=False)
    assert res2["violation_count"] == 0 and ds.summarize_for_user(res2) is None
    print("PASS test_sweep_targets_shape_and_summary")


def main() -> int:
    test_find_candidate_text_includes_deliverables_excludes_infra()
    test_find_candidate_deliverables_merges_docx_and_md()
    test_scan_text_file_catches_and_shapes()
    test_scan_text_file_clean_and_unreadable()
    test_scan_path_for_violations_dispatches()
    test_sweep_workspace_flags_md_not_infra_and_readonly()
    test_sweep_targets_shape_and_summary()
    print("\nALL deliverable_sweep_text tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
