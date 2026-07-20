#!/usr/bin/env python3
"""Tests for docx_leak_scanner (v3.13.8 — Bug #57 + #59 + #54)."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from docx_leak_scanner import (  # noqa: E402
    LeakScanError,
    collect_docx_leaks,
    scan_docx_for_leaks,
)


def _build_synthetic_docx(body_xml_inner: str) -> Path:
    """Create a minimal valid .docx with arbitrary inner body XML for testing.

    Uses a stripped-down OPC structure: just [Content_Types].xml, _rels/.rels,
    and word/document.xml. Real briefs from brief_writer use python-docx and
    have additional parts (styles.xml etc) — those are not required by the
    scanner since it only reads word/document.xml.
    """
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    document_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body>" + body_xml_inner + "</w:body></w:document>"
    )
    tmp = Path(tempfile.mkdtemp(prefix="leakscan_test_"))
    out = tmp / "synthetic.docx"
    with zipfile.ZipFile(str(out), "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels)
        z.writestr("word/document.xml", document_xml)
    return out


def test_clean_doc_returns_empty() -> None:
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>This is a perfectly normal sentence about widgets.</w:t></w:r></w:p>"
    )
    findings = collect_docx_leaks(docx)
    assert findings == [], findings
    # scan version with raise should NOT raise on a clean doc
    scan_docx_for_leaks(docx)
    print("PASS test_clean_doc_returns_empty")


def test_project_id_leak_detected() -> None:
    """Bug #57 — project_020 in body prose."""
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>The project_020 work continues this week.</w:t></w:r></w:p>"
    )
    try:
        scan_docx_for_leaks(docx)
        raise AssertionError("expected LeakScanError to fire on project_020")
    except LeakScanError as e:
        assert "project_020" in str(e), str(e)
    print("PASS test_project_id_leak_detected")


def test_substrate_path_leak_detected() -> None:
    """Bug #59 — events.jsonl in body prose."""
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>Refer to events.jsonl for the canonical history.</w:t></w:r></w:p>"
    )
    try:
        scan_docx_for_leaks(docx)
        raise AssertionError("expected LeakScanError on events.jsonl")
    except LeakScanError as e:
        assert "events.jsonl" in str(e), str(e)
    print("PASS test_substrate_path_leak_detected")


def test_word_boundary_anchored_no_false_positive() -> None:
    """Bug #54 — 'TTL' inside 'settled' should NOT flag, but 'Tier 3' should."""
    # 'settled' contains 'ttl' but not 'TTL' as standalone — also not in our patterns.
    # Better example: 'project20' (no underscore) should NOT match 'project_\d+'
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>The project20 line is fine; settle the deal.</w:t></w:r></w:p>"
    )
    findings = collect_docx_leaks(docx)
    assert findings == [], findings
    print("PASS test_word_boundary_anchored_no_false_positive")


def test_token_split_across_runs_detected() -> None:
    """Bug #54 — 'ecosystem' split as 'eco' + 'system' across runs."""
    docx = _build_synthetic_docx(
        "<w:p>"
        "<w:r><w:t>The eco</w:t></w:r>"
        "<w:r><w:t>system thrives.</w:t></w:r>"
        "</w:p>"
    )
    try:
        scan_docx_for_leaks(docx)
        raise AssertionError("expected LeakScanError on split ecosystem")
    except LeakScanError as e:
        assert "ecosystem" in str(e), str(e)
    print("PASS test_token_split_across_runs_detected")


def test_phase_n_voice_leak_detected() -> None:
    """Bug #16 — 'Phase 3' is process narration that shouldn't appear in briefs."""
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>This was Phase 3 of the migration.</w:t></w:r></w:p>"
    )
    try:
        scan_docx_for_leaks(docx)
        raise AssertionError("expected LeakScanError on Phase 3")
    except LeakScanError:
        pass
    print("PASS test_phase_n_voice_leak_detected")


def test_marketing_word_leak_detected() -> None:
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>The synergy across the team is great.</w:t></w:r></w:p>"
    )
    try:
        scan_docx_for_leaks(docx)
        raise AssertionError("expected LeakScanError on synergy")
    except LeakScanError as e:
        assert "synergy" in str(e), str(e)
    print("PASS test_marketing_word_leak_detected")


def test_collect_does_not_raise() -> None:
    """collect_docx_leaks returns findings rather than raising — used by
    auditors that want a list rather than an exception."""
    docx = _build_synthetic_docx(
        "<w:p><w:r><w:t>project_007 is mentioned.</w:t></w:r></w:p>"
    )
    findings = collect_docx_leaks(docx)
    assert len(findings) == 1, findings
    assert findings[0]["match"] == "project_007", findings[0]
    print("PASS test_collect_does_not_raise")


def test_table_cells_exempt_from_voice_dash_ban() -> None:
    """FB-16 second-eyes fix (2026-07-19): the dash-as-punctuation FAIL applies
    to body PROSE only. A .docx table cell with an en-dash range must NOT set
    has_violation via the voice scan — _docx_paragraph_text strips <w:tbl>
    regions before the voice pass. The SAME dash in a body paragraph fails.
    The LEAK scan still sees table text (full-document _normalize_for_scan)."""
    from docx_leak_scanner import scan_docx_for_violations

    cell_p = "<w:p><w:r><w:t>Q2 – Q3 revenue: 10 — 20</w:t></w:r></w:p>"
    table = ("<w:tbl><w:tr><w:tc>" + cell_p + "</w:tc></w:tr></w:tbl>"
             "<w:p><w:r><w:t>A clean body sentence.</w:t></w:r></w:p>")
    docx = _build_synthetic_docx(table)
    result = scan_docx_for_violations(docx)
    assert not result["has_violation"], result
    assert not any(f["rule"] == "dash_as_punctuation"
                   for f in result["voice"]["findings"]), result["voice"]

    body = "<w:p><w:r><w:t>We shipped — fast.</w:t></w:r></w:p>"
    docx2 = _build_synthetic_docx(body)
    result2 = scan_docx_for_violations(docx2)
    assert result2["has_violation"], result2
    assert any(f["rule"] == "dash_as_punctuation"
               for f in result2["voice"]["findings"]), result2["voice"]

    # leak scan still covers table text: plant a leak token inside a cell
    leak_cell = ("<w:tbl><w:tr><w:tc><w:p><w:r><w:t>project_007 hides here"
                 "</w:t></w:r></w:p></w:tc></w:tr></w:tbl>")
    docx3 = _build_synthetic_docx(leak_cell)
    findings = collect_docx_leaks(docx3)
    assert any(f["match"] == "project_007" for f in findings), findings
    print("PASS test_table_cells_exempt_from_voice_dash_ban")


def main() -> int:
    test_clean_doc_returns_empty()
    test_project_id_leak_detected()
    test_substrate_path_leak_detected()
    test_word_boundary_anchored_no_false_positive()
    test_token_split_across_runs_detected()
    test_phase_n_voice_leak_detected()
    test_marketing_word_leak_detected()
    test_collect_does_not_raise()
    test_table_cells_exempt_from_voice_dash_ban()
    print("\nALL docx_leak_scanner tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
