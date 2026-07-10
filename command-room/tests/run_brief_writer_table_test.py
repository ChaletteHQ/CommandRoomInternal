#!/usr/bin/env python3
"""Tests for brief_writer table + matrix primitives (Bug #58, v3.13.8)."""

from __future__ import annotations

import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

from brief_writer import make_brief  # noqa: E402


def _extract_xml(path: Path) -> str:
    with zipfile.ZipFile(str(path)) as z:
        return z.read("word/document.xml").decode("utf-8")


def test_basic_table_renders() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="brief_table_test_"))
    out = tmp / "brief.docx"
    make_brief(
        str(out),
        brief_kind="memo",
        title="Test memo",
        subtitle="Test subtitle",
        exec_header={"verdict": "Table primitive renders."},  # OUT2 §4 flip
        sections=[
            {
                "heading": "Section A",
                "body": "Body text.",
                "table": {
                    "headers": ["Col1", "Col2", "Col3"],
                    "rows": [
                        ["a1", "a2", "a3"],
                        ["b1", "b2", "b3"],
                        ["c1", "c2", "c3"],
                    ],
                    "highlight_row_idx": 1,
                },
            },
        ],
        contract="off",  # B3: this test verifies table RENDERING, not the contract
    )
    assert out.exists()
    xml = _extract_xml(out)
    # Table element should appear
    assert "<w:tbl" in xml, "expected table XML"
    # Headers should appear
    assert "Col1" in xml and "Col2" in xml and "Col3" in xml
    # Cells should appear
    assert "a1" in xml and "b2" in xml and "c3" in xml
    print("PASS test_basic_table_renders")


def test_matrix_with_star_renders() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="brief_matrix_test_"))
    out = tmp / "matrix.docx"
    make_brief(
        str(out),
        brief_kind="decision_memo",
        title="Decision test",
        subtitle="Test subtitle",
        exec_header={"verdict": "Matrix primitive renders."},  # OUT2 §4 flip
        sections=[
            {
                "heading": "Comparison",
                "body": "Comparing options.",
                "matrix": {
                    "headers_row": ["Option A", "Option B", "Option C"],
                    "headers_col": ["Cost", "Risk", "Time"],
                    "cells": [
                        ["$10k", "$20k", "$15k"],
                        ["low", "high", "med"],
                        ["2wk", "1mo", "3wk"],
                    ],
                    "star_col_idx": 0,
                },
            },
        ],
        contract="off",  # B3: this test verifies matrix RENDERING, not the contract
    )
    assert out.exists()
    xml = _extract_xml(out)
    assert "<w:tbl" in xml
    assert "Option A" in xml and "Cost" in xml
    # Star glyph on column 0 cells
    assert "★" in xml, "expected star glyph in matrix col 0"
    print("PASS test_matrix_with_star_renders")


def test_contract_review_flag_matrix() -> None:
    """SPEC OUT1 §4 — contract-review 'How it compares' matrix: the flag column
    (flag_col_idx) gets shaded with the brand tint that matches each flag word."""
    from brand import DEFAULT_BRAND
    tmp = Path(tempfile.mkdtemp(prefix="brief_flag_matrix_"))
    out = tmp / "flag_matrix.docx"
    make_brief(
        str(out),
        brief_kind="contract_review",
        title="Acme MSA review",
        subtitle="Test subtitle",
        exec_header={"verdict": "Flag matrix renders."},  # OUT2 §4 flip (contract_review is now a STANDARD_KIND)
        sections=[
            {
                "heading": "How it compares",
                "matrix": {
                    "headers_row": ["Your standard", "This contract", "Flag"],
                    "headers_col": ["Term length", "Indemnification", "Payment"],
                    "cells": [
                        ["12 months", "12 months", "Standard"],
                        ["mutual, capped", "one-way, uncapped", "Flag"],
                        ["net 30", "net 45", "Review"],
                    ],
                    "flag_col_idx": 2,
                },
            },
        ],
        contract="off",
        voice_gate="off",
    )
    assert out.exists()
    xml = _extract_xml(out)
    # Each flag word's tint fill must appear in the doc.
    pal = DEFAULT_BRAND["palette"]
    for tint_key in ("flag_ok", "flag_bad", "flag_warn"):
        assert pal[tint_key].upper() in xml.upper(), f"expected {tint_key} tint in flag matrix"
    # The flag words themselves render (grayscale/colorblind-safe).
    assert "Standard" in xml and "Flag" in xml and "Review" in xml
    print("PASS test_contract_review_flag_matrix")


def test_insights_kind_supported() -> None:
    """Bug #26 — insight-generator can use brief_writer with insights kind."""
    tmp = Path(tempfile.mkdtemp(prefix="brief_insights_test_"))
    out = tmp / "insights.docx"
    make_brief(
        str(out),
        brief_kind="insights",
        title="Workspace insights",
        subtitle="Weekly observations",
        sections=[
            {"heading": "Observation 1", "body": "Lorem ipsum."},
        ],
        contract="off",  # B3: insights has no contract rules; placeholder text is intentional filler
    )
    assert out.exists()
    xml = _extract_xml(out)
    assert "INSIGHTS" in xml
    print("PASS test_insights_kind_supported")


def test_table_only_section_works() -> None:
    """A section with just a table (no body/bullets) should render."""
    tmp = Path(tempfile.mkdtemp(prefix="brief_table_only_"))
    out = tmp / "table_only.docx"
    make_brief(
        str(out),
        brief_kind="board_pack",
        title="Board pack test",
        subtitle="Q2 metrics",
        exec_header={"verdict": "Timeline primitive renders."},  # OUT2 §4 flip
        sections=[
            {
                "heading": "KPIs",
                "table": {
                    "headers": ["Metric", "Q1", "Q2"],
                    "rows": [
                        ["Revenue", "$100k", "$120k"],
                        ["Margin", "32%", "38%"],
                    ],
                },
            },
        ],
    )
    assert out.exists()
    print("PASS test_table_only_section_works")


def main() -> int:
    test_basic_table_renders()
    test_matrix_with_star_renders()
    test_contract_review_flag_matrix()
    test_insights_kind_supported()
    test_table_only_section_works()
    print("\nALL brief_writer table/matrix tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
