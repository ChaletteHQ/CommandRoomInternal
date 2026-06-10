#!/usr/bin/env python3
"""
Polished brief docx writer (v2.14.32+).

Single source of truth for Command Room meeting brief layout. Both Call_Prep
and Past_Meeting briefs go through `make_brief()`. Replaces the v2.14.31-era
flow where each fire asked the docx skill to lay out the document and got
slightly different output every time.

Design goals:
  - Deterministic. Same structured input -> same docx output, every fire.
  - Forwardable-clean per `meeting-notes/SKILL.md` Brief Authoring Rules.
    No provenance footers, no internal IDs, no calendar URLs.
  - Visually polished but quiet. Subtle colors, generous whitespace,
    a single horizontal rule under the header. No emoji, no logos,
    no loud branding.
  - One US Letter page friendly (margins + font sizes tuned for density).

Used by:
  - orchestrator-upcoming-meetings.md (Phase 4 step 3 -> Call_Prep_*.docx)
  - orchestrator-past-meetings.md (Phase 4 step 7 -> Past_Meeting_*.docx)
  - meeting-notes/SKILL.md Step 9a (on-demand Past_Meeting_*.docx)

Dependencies: python-docx (pinned to PYTHON_DOCX_PIN below). Self-installs
on import failure (idempotent, ~3s cold, no-op when already present). The
pin must match what the brief_writer test suite is run against — see
README "Requirements" section for how to pre-install in locked-down
environments.
"""
from __future__ import annotations

import json
import subprocess
import sys
from typing import List, Dict, Optional, Union


PYTHON_DOCX_PIN = "1.2.0"


def _ensure_python_docx() -> None:
    try:
        import docx  # noqa: F401
    except ImportError:
        print(
            f"Installing python-docx (=={PYTHON_DOCX_PIN}) — one-time setup. "
            "(Plugin requires this for .docx generation. "
            "See README Requirements section to pre-install in locked-down environments.)",
            file=sys.stderr,
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--quiet",
                f"python-docx=={PYTHON_DOCX_PIN}",
            ],
            check=True,
        )


_ensure_python_docx()

from docx import Document  # noqa: E402
from docx.shared import Pt, Inches, RGBColor  # noqa: E402
from docx.enum.text import WD_ALIGN_PARAGRAPH  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402


# ---------- Brand palette (subtle, professional) ----------

INK = RGBColor(0x1A, 0x1A, 0x1A)        # body text — near-black
HEADING = RGBColor(0x0F, 0x2A, 0x3F)    # section headings — dark navy
MUTED = RGBColor(0x6B, 0x6B, 0x6B)      # subtitle, footer — medium grey
ACCENT = RGBColor(0x2E, 0x7D, 0x6B)     # eyebrow label — dark teal
RULE_HEX = "C0C0C0"                     # horizontal rule — light grey

BODY_FONT = "Calibri"
HEADING_FONT = "Calibri"


# ---------- Internal helpers ----------

def _set_run(run, *, font=BODY_FONT, size=11, color=INK, bold=False, italic=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.font.color.rgb = color
    run.font.bold = bold
    run.font.italic = italic


def _add_horizontal_rule(paragraph) -> None:
    """Single light-grey rule below the given paragraph."""
    p = paragraph._p
    pPr = p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), "6")
    bottom.set(qn("w:space"), "1")
    bottom.set(qn("w:color"), RULE_HEX)
    pBdr.append(bottom)
    pPr.append(pBdr)


def _set_normal_baseline(doc) -> None:
    normal = doc.styles["Normal"]
    normal.font.name = BODY_FONT
    normal.font.size = Pt(11)
    normal.font.color.rgb = INK


def _tighten_margins(doc) -> None:
    for section in doc.sections:
        section.top_margin = Inches(0.9)
        section.bottom_margin = Inches(0.9)
        section.left_margin = Inches(1.0)
        section.right_margin = Inches(1.0)


def _add_eyebrow(doc, label: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(label.upper())
    _set_run(run, font=HEADING_FONT, size=9, color=ACCENT, bold=True)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)


def _add_title(doc, title: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(title)
    _set_run(run, font=HEADING_FONT, size=22, color=HEADING, bold=True)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.15


def _add_subtitle(doc, subtitle: str) -> None:
    p = doc.add_paragraph()
    run = p.add_run(subtitle)
    _set_run(run, font=BODY_FONT, size=11, color=MUTED)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(8)


def _add_header_rule(doc) -> None:
    p = doc.add_paragraph()
    _add_horizontal_rule(p)
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(10)


def _add_section_heading(doc, text: str, level: int = 2) -> None:
    """v3.13.8+ — uses python-docx's actual Heading{level} style so Word's
    TOC, accessibility tooling, and outline panes see real headings (Bug #7).

    Pre-v3.13.8 this function rendered headings as bold runs inside Normal
    paragraphs, which looked right but didn't register as headings in
    Word's structure. Now we apply the Heading2 style and then override
    font/color so the canonical navy + Calibri 13pt look stays intact.
    """
    p = doc.add_heading(level=level)
    run = p.add_run(text)
    _set_run(run, font=HEADING_FONT, size=13, color=HEADING, bold=True)
    p.paragraph_format.space_before = Pt(14)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.keep_with_next = True


def _add_body_paragraphs(doc, body_text: str) -> None:
    """Split on blank lines; each block becomes one paragraph."""
    for block in body_text.split("\n\n"):
        block = block.strip()
        if not block:
            continue
        p = doc.add_paragraph()
        run = p.add_run(block)
        _set_run(run, font=BODY_FONT, size=11, color=INK)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(6)
        p.paragraph_format.line_spacing = 1.25


def _shade_cell(cell, hex_color: str) -> None:
    """Apply a background shading color to a table cell."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), hex_color)
    tc_pr.append(shd)


def _add_table(
    doc,
    rows: List[List[str]],
    headers: Optional[List[str]] = None,
    highlight_row_idx: Optional[int] = None,
    column_widths: Optional[List[float]] = None,
) -> None:
    """v3.13.8+ — table primitive for brief_writer.

    Args:
      rows: list of lists — table data rows (each inner list = one row).
      headers: optional list — header row content. When provided, gets
        navy heading style + bold white text + slightly heavier height.
      highlight_row_idx: optional int — row index within `rows` (0-based,
        excluding the header) to visually highlight, e.g. the recommended
        option in a comparison matrix. Renders with a subtle ACCENT-tinted
        background.
      column_widths: optional list of float (inches). Defaults to equal split
        across 6 inches usable page width.
    """
    if not rows:
        raise ValueError("table 'rows' must be a non-empty list")
    if not all(isinstance(r, list) for r in rows):
        raise ValueError("each row must be a list")

    n_cols = max(len(r) for r in rows)
    if headers is not None:
        n_cols = max(n_cols, len(headers))

    total_rows = len(rows) + (1 if headers else 0)
    table = doc.add_table(rows=total_rows, cols=n_cols)
    table.style = "Table Grid"

    # Column widths
    if column_widths:
        if len(column_widths) != n_cols:
            raise ValueError(
                f"column_widths length {len(column_widths)} must match n_cols {n_cols}"
            )
        for i, width in enumerate(column_widths):
            for row in table.rows:
                row.cells[i].width = Inches(width)
    else:
        per_col = 6.0 / n_cols
        for i in range(n_cols):
            for row in table.rows:
                row.cells[i].width = Inches(per_col)

    # Header row
    row_offset = 0
    if headers:
        for j, header_text in enumerate(headers):
            cell = table.rows[0].cells[j]
            _shade_cell(cell, "0F2A3F")  # HEADING navy
            for p in cell.paragraphs:
                p.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(header_text))
            _set_run(run, font=HEADING_FONT, size=10, color=RGBColor(0xFF, 0xFF, 0xFF), bold=True)
        row_offset = 1

    # Data rows
    for i, row_data in enumerate(rows):
        target_row = table.rows[i + row_offset]
        is_highlight = highlight_row_idx is not None and i == highlight_row_idx
        # Zebra stripe (very subtle) when no per-row highlight has consumed it
        zebra_fill = "F5F2EE" if (i % 2 == 1) else None
        accent_fill = "E8F1EE"  # ACCENT-tinted background
        for j in range(n_cols):
            cell = target_row.cells[j]
            text = str(row_data[j]) if j < len(row_data) else ""
            if is_highlight:
                _shade_cell(cell, accent_fill)
            elif zebra_fill:
                _shade_cell(cell, zebra_fill)
            for p in cell.paragraphs:
                p.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(text)
            _set_run(
                run,
                font=BODY_FONT,
                size=10,
                color=INK,
                bold=is_highlight,
            )

    # Space after the table
    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(6)


def _add_matrix(
    doc,
    cells: Union[List[List[str]], Dict[tuple, str]],
    headers_row: Optional[List[str]] = None,
    headers_col: Optional[List[str]] = None,
    star_col_idx: Optional[int] = None,
) -> None:
    """v3.13.8+ — N×M comparison matrix with optional ★ glyph highlighting.

    Args:
      cells: either a 2D list of strings OR a dict {(row, col): value}.
      headers_row: optional column-header labels (top row).
      headers_col: optional row-header labels (leftmost column).
      star_col_idx: optional int — render ★ glyph in front of every cell
        whose row's value in this column is non-empty/non-falsy (used for
        "recommended option" matrices).
    """
    if isinstance(cells, dict):
        n_rows = max(k[0] for k in cells.keys()) + 1 if cells else 0
        n_cols = max(k[1] for k in cells.keys()) + 1 if cells else 0
        as_list = [["" for _ in range(n_cols)] for _ in range(n_rows)]
        for (r, c), v in cells.items():
            as_list[r][c] = v
        rows_data = as_list
    else:
        rows_data = cells

    if not rows_data:
        raise ValueError("matrix 'cells' must contain at least one row")

    n_cols = max(len(r) for r in rows_data)
    if headers_row is not None:
        n_cols = max(n_cols, len(headers_row))
    # +1 column for headers_col if present
    actual_cols = n_cols + (1 if headers_col else 0)
    total_rows = len(rows_data) + (1 if headers_row else 0)
    table = doc.add_table(rows=total_rows, cols=actual_cols)
    table.style = "Table Grid"

    per_col = 6.0 / actual_cols
    for i in range(actual_cols):
        for row in table.rows:
            row.cells[i].width = Inches(per_col)

    # Header row
    row_offset = 0
    if headers_row:
        if headers_col:
            cell = table.rows[0].cells[0]
            _shade_cell(cell, "0F2A3F")
        for j, header_text in enumerate(headers_row):
            target_j = j + (1 if headers_col else 0)
            cell = table.rows[0].cells[target_j]
            _shade_cell(cell, "0F2A3F")
            for p in cell.paragraphs:
                p.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(header_text))
            _set_run(run, font=HEADING_FONT, size=10, color=RGBColor(0xFF, 0xFF, 0xFF), bold=True)
        row_offset = 1

    # Data + headers_col
    for i, row_data in enumerate(rows_data):
        target_row = table.rows[i + row_offset]
        if headers_col:
            label = headers_col[i] if i < len(headers_col) else ""
            cell = target_row.cells[0]
            _shade_cell(cell, "F0EDE9")
            for p in cell.paragraphs:
                p.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(str(label))
            _set_run(run, font=HEADING_FONT, size=10, color=HEADING, bold=True)
        for j in range(n_cols):
            target_j = j + (1 if headers_col else 0)
            cell = target_row.cells[target_j]
            text = str(row_data[j]) if j < len(row_data) else ""
            if star_col_idx is not None and j == star_col_idx and text.strip():
                text = "★ " + text
            for p in cell.paragraphs:
                p.text = ""
            p = cell.paragraphs[0]
            run = p.add_run(text)
            _set_run(run, font=BODY_FONT, size=10, color=INK)

    spacer = doc.add_paragraph()
    spacer.paragraph_format.space_before = Pt(0)
    spacer.paragraph_format.space_after = Pt(6)


def _add_bullets(doc, bullets: List[str]) -> None:
    for line in bullets:
        line = line.strip()
        if not line:
            continue
        # Strip leading bullet markers if the caller already added them.
        if line.startswith(("- ", "* ", "• ")):
            line = line[2:].lstrip()
        p = doc.add_paragraph(style="List Bullet")
        run = p.add_run(line)
        _set_run(run, font=BODY_FONT, size=11, color=INK)
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2)
        p.paragraph_format.line_spacing = 1.20


def _add_footer(doc, text: str) -> None:
    footer = doc.sections[0].footer
    fp = footer.paragraphs[0]
    fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = fp.add_run(text)
    _set_run(run, font=BODY_FONT, size=9, color=MUTED)


# ---------- Public API ----------

def make_brief(
    output_path: str,
    *,
    brief_kind: str,
    title: str,
    subtitle: str,
    sections: List[Dict[str, Union[str, List[str]]]],
    footer_text: str = "Command Room",
) -> str:
    """Write a polished brief .docx to `output_path` and return the path.

    Args:
      output_path: absolute path to write the .docx (already resolved via
        `brief_path.get_brief_path()`).
      brief_kind: one of the supported kinds. Drives the eyebrow label
        only — layout is identical across kinds. Pre-v3.10.0 only
        "call_prep" and "past_meeting" were supported; v3.10.0+ extends
        the set to cover the v3.8.0 new-skill deliverables.

        Supported values:
          "call_prep"          → eyebrow "CALL PREP"
          "past_meeting"       → eyebrow "MEETING BRIEF"
          "board_pack"         → eyebrow "BOARD PACK"
          "contract_review"    → eyebrow "CONTRACT REVIEW"
          "decision_memo"      → eyebrow "DECISION MEMO"
          "operator_report"    → eyebrow "OPERATING LIFT"
          "weekly_recap"       → eyebrow "WEEKLY RECAP"
          "weekly_audit"       → eyebrow "WEEKLY AUDIT"
          "dormant_scan"       → eyebrow "DORMANT CUSTOMER SCAN"
          "automation_scan"    → eyebrow "AUTOMATION SCAN"
          "automation_recipe"  → eyebrow "AUTOMATION SETUP RECIPE"
          "followup_pack"      → eyebrow "FOLLOW-UP PACK"
          "memo"               → eyebrow "MEMO"
          "one_pager"          → eyebrow "ONE-PAGER"
          "insights"           → eyebrow "INSIGHTS"
          "stress_test"        → eyebrow "STRESS TEST"
      title: top-of-page title. Should resolve all entity IDs to names
        (e.g. "Sam Sample — Q2 deck review", not "person_037 — ...").
      subtitle: one-line meta below title (e.g. "Fri, May 9 · 9:00 AM PT
        · Category Company"). Plain text, no formatting.
      sections: ordered list of section dicts. Each dict has:
          - "heading": str — section title
          - "body": str (optional) — paragraph-style content; blank lines
            become paragraph breaks
          - "bullets": list[str] (optional) — bullet items
          - "table": dict (optional) — {rows: list[list], headers?: list,
            highlight_row_idx?: int, column_widths?: list[float]}
          - "matrix": dict (optional) — {cells: 2D list or {(r,c): v} dict,
            headers_row?: list, headers_col?: list, star_col_idx?: int}
        A section may mix body / bullets / table / matrix. Render order is:
        body → bullets → table → matrix.
      footer_text: centered footer text on every page. Defaults to
        "Command Room". NEVER include provenance metadata
        (Source / Fired / meeting_id / TTL) per CONTRACT.md Rule 15.

    Returns: `output_path` on success.

    Raises: ValueError on bad inputs. Lets python-docx errors propagate.
    """
    eyebrow_by_kind = {
        "call_prep":         "CALL PREP",
        "past_meeting":      "MEETING BRIEF",
        "board_pack":        "BOARD PACK",
        "contract_review":   "CONTRACT REVIEW",
        "decision_memo":     "DECISION MEMO",
        "operator_report":   "OPERATING LIFT",
        "weekly_recap":      "WEEKLY RECAP",
        "weekly_audit":      "WEEKLY AUDIT",
        "dormant_scan":      "DORMANT CUSTOMER SCAN",
        "automation_scan":   "AUTOMATION SCAN",
        "automation_recipe": "AUTOMATION SETUP RECIPE",
        "followup_pack":     "FOLLOW-UP PACK",
        "memo":              "MEMO",
        "one_pager":         "ONE-PAGER",
        "insights":          "INSIGHTS",
        "stress_test":       "STRESS TEST",
    }
    if brief_kind not in eyebrow_by_kind:
        raise ValueError(
            f"brief_kind must be one of {sorted(eyebrow_by_kind)}, "
            f"got {brief_kind!r}"
        )
    if not title:
        raise ValueError("title is required")
    if not subtitle:
        raise ValueError("subtitle is required")
    if not isinstance(sections, list) or not sections:
        raise ValueError("sections must be a non-empty list")

    doc = Document()
    _set_normal_baseline(doc)
    _tighten_margins(doc)

    eyebrow_label = eyebrow_by_kind[brief_kind]
    _add_eyebrow(doc, eyebrow_label)
    _add_title(doc, title)
    _add_subtitle(doc, subtitle)
    _add_header_rule(doc)

    for sec in sections:
        heading = sec.get("heading")
        if not heading:
            raise ValueError(f"section missing 'heading': {sec!r}")
        _add_section_heading(doc, heading)
        body = sec.get("body")
        bullets = sec.get("bullets")
        table = sec.get("table")
        matrix = sec.get("matrix")
        if body:
            if not isinstance(body, str):
                raise ValueError(f"section 'body' must be a string: {sec!r}")
            _add_body_paragraphs(doc, body)
        if bullets:
            if not isinstance(bullets, list):
                raise ValueError(f"section 'bullets' must be a list: {sec!r}")
            _add_bullets(doc, bullets)
        if table:
            if not isinstance(table, dict):
                raise ValueError(f"section 'table' must be a dict: {sec!r}")
            _add_table(
                doc,
                rows=table["rows"],
                headers=table.get("headers"),
                highlight_row_idx=table.get("highlight_row_idx"),
                column_widths=table.get("column_widths"),
            )
        if matrix:
            if not isinstance(matrix, dict):
                raise ValueError(f"section 'matrix' must be a dict: {sec!r}")
            _add_matrix(
                doc,
                cells=matrix["cells"],
                headers_row=matrix.get("headers_row"),
                headers_col=matrix.get("headers_col"),
                star_col_idx=matrix.get("star_col_idx"),
            )
        if not body and not bullets and not table and not matrix:
            raise ValueError(
                f"section needs 'body', 'bullets', 'table', or 'matrix': {sec!r}"
            )

    _add_footer(doc, footer_text)
    doc.save(output_path)

    # v3.13.8+ — universal post-render leak scan gate (Bug #57 + #59 + #54).
    # Runs against every brief regardless of which skill called us. The
    # scanner is added by §2.4. We invoke it lazily to avoid a hard
    # dependency cycle during partial-install scenarios.
    try:
        from docx_leak_scanner import scan_docx_for_leaks, LeakScanError
        scan_docx_for_leaks(output_path)
    except ImportError:
        # docx_leak_scanner not installed yet (e.g. a workspace that hasn't
        # taken the v3.13.8 update). Don't block briefs; the scanner will
        # apply on the next plugin update.
        pass

    return output_path


def make_brief_from_json(json_payload: str) -> str:
    """JSON wrapper for orchestrator bash invocations.

    Pipe a JSON object on stdin OR pass as the first CLI arg.
    Required keys mirror `make_brief()` kwargs.

    Returns the output path (also printed to stdout for shell capture).
    """
    payload = json.loads(json_payload)
    path = make_brief(
        payload["output_path"],
        brief_kind=payload["brief_kind"],
        title=payload["title"],
        subtitle=payload["subtitle"],
        sections=payload["sections"],
        footer_text=payload.get("footer_text", "Command Room"),
    )
    print(path)
    return path


__all__ = ["make_brief", "make_brief_from_json"]


if __name__ == "__main__":
    # CLI: `python3 brief_writer.py '<json>'` OR pipe JSON on stdin.
    if len(sys.argv) > 1:
        make_brief_from_json(sys.argv[1])
    else:
        make_brief_from_json(sys.stdin.read())
