#!/usr/bin/env python3
"""Shared component library — one implementation, two backends (SPEC OUT2 §2a).

WHY THIS EXISTS
---------------
Before OUT2 the visual-component data shapes (stat tiles, relationship
timeline, table, comparison matrix) and their validation rules lived inside
`brief_writer.py` (the .docx backend), and the chat widget grew its own
hand-built header-counter markup inside `chat_output_renderer.py`. Same
concepts, two implementations, guaranteed drift (F-60: "same tiles in chat
and in the doc" was two code paths).

This module is now the ONE owner of:

  1. The component DATA SHAPES + validation rules:
       - tiles      [{label, value}]            (1-5 per band; empty REFUSED)
       - timeline   [{date, label, current?}]   (>= 2 points or nothing)
       - table      {rows, headers?, ...}       (non-empty; rows are lists)
       - matrix     {cells, headers_row?, ...}  (2D list or {(r,c): v} dict)
     The drop-empty rules, the one-point-strip refusal, and the matrix
     star-highlight logic moved here from brief_writer VERBATIM — logic
     identical, imports updated (SPEC OUT2 §2a).

  2. The HTML FRAGMENT builders for widget surfaces (tile band +
     two-column table) matching `shared/CHAT_ACTION_WIDGET.md` styling.
     `chat_output_renderer.py` consumes `build_tile_band_html` for both its
     legacy `counters` path and the OUT2 `tiles` path, so the chat band and
     the .docx band are finally one implementation fed by one shape.

TWO BACKENDS, ONE SHAPE
-----------------------
  - .docx backend: `brief_writer.py` — imports the validators here, renders
    via python-docx, colors/fonts resolved through `brand.get_brand()`
    (SPEC OUT1).
  - HTML backend: the fragment builders below — markup-only by default
    (the widget's `_WIDGET_CSS` styles the classes), or self-contained with
    `include_style=True`, in which case colors/fonts ALSO resolve through
    `brand.get_brand()` (SPEC OUT2 §2c — no palette constants live here).

The Command Room chat widget's dark product chrome (`_WIDGET_CSS` in
chat_output_renderer.py) is deliberately NOT part of the brand layer — it is
product UI, not a client deliverable surface. The stray-palette guard
(`tests/run_no_stray_palette_test.py`) allowlists it as such.

Stdlib only (brand.py is stdlib only too).
"""
from __future__ import annotations

import html as _html
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Union

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brand import get_brand  # noqa: E402


# ---------------------------------------------------------------------------
# Tiles — the stat-tile band (moved from brief_writer._add_stat_tiles,
# validation logic identical)
# ---------------------------------------------------------------------------

MAX_TILES_PER_BAND = 5


def validate_tiles(tiles: List[Dict[str, str]]) -> None:
    """The stat-tile band contract (v4.5.2 S1 drop rule, M directive).

    1-5 tiles of {label, value}. REFUSES an empty tile — "a tile with no
    data is dropped, never rendered as an empty frame" is enforced by the
    builder (prep_pipeline.build_prep_tiles) AND here at the render
    chokepoint, so an empty frame is structurally impossible whichever
    backend called us. Raises ValueError; returns None on a clean band.
    """
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("tiles must be a non-empty list of {label, value}")
    if len(tiles) > MAX_TILES_PER_BAND:
        raise ValueError(
            f"at most {MAX_TILES_PER_BAND} tiles per band (got {len(tiles)})"
        )
    for t in tiles:
        if not isinstance(t, dict) or not str(t.get("label") or "").strip() \
                or not str(t.get("value") or "").strip():
            raise ValueError(
                f"tile with no data must be DROPPED by the caller, never "
                f"rendered as an empty frame: {t!r}"
            )


# ---------------------------------------------------------------------------
# Timeline — the relationship timeline strip (moved from
# brief_writer._add_timeline, validation logic identical)
# ---------------------------------------------------------------------------

def validate_timeline(points: List[Dict[str, str]]) -> None:
    """The timeline-strip contract (v4.5.2 S1).

    Requires >= 2 points of {date, label} — a one-point strip is an empty
    frame; the builder (prep_pipeline.build_relationship_timeline) already
    drops it, and this render-chokepoint check makes the rule bypass-proof.
    Raises ValueError; returns None on a clean strip.
    """
    if not isinstance(points, list) or len(points) < 2:
        raise ValueError(
            "timeline needs >= 2 points; the caller drops the section when "
            "the substrate has fewer (never render an empty strip)"
        )
    for pt in points:
        if not isinstance(pt, dict) or not str(pt.get("date") or "").strip() \
                or not str(pt.get("label") or "").strip():
            raise ValueError(f"timeline point needs 'date' and 'label': {pt!r}")


# ---------------------------------------------------------------------------
# Table — validation (moved from brief_writer._add_table, logic identical)
# ---------------------------------------------------------------------------

def validate_table(
    rows: List[List[str]],
    headers: Optional[List[str]] = None,
    column_widths: Optional[List[float]] = None,
) -> int:
    """Validate a table payload and return its column count.

    rows must be a non-empty list of lists; when column_widths is passed its
    length must match the computed column count. Raises ValueError (same
    messages the docx backend always raised); returns n_cols.
    """
    if not rows:
        raise ValueError("table 'rows' must be a non-empty list")
    if not all(isinstance(r, list) for r in rows):
        raise ValueError("each row must be a list")
    n_cols = max(len(r) for r in rows)
    if headers is not None:
        n_cols = max(n_cols, len(headers))
    if column_widths and len(column_widths) != n_cols:
        raise ValueError(
            f"column_widths length {len(column_widths)} must match n_cols {n_cols}"
        )
    return n_cols


# ---------------------------------------------------------------------------
# Matrix — normalization + star-highlight + flag words (moved from
# brief_writer._add_matrix / _FLAG_TINTS / _flag_tint_for, logic identical)
# ---------------------------------------------------------------------------

def normalize_matrix(
    cells: Union[List[List[str]], Dict[tuple, str]],
    headers_row: Optional[List[str]] = None,
) -> Tuple[List[List[str]], int]:
    """Normalize a matrix `cells` payload to (rows_data, n_cols).

    Accepts a 2D list of strings OR a sparse {(row, col): value} dict (a
    missing key becomes ""). Raises ValueError when the result has no rows.
    n_cols accounts for headers_row when it is wider than the data.
    """
    if isinstance(cells, dict):
        n_rows = max(k[0] for k in cells.keys()) + 1 if cells else 0
        n_cols = max(k[1] for k in cells.keys()) + 1 if cells else 0
        as_list: List[List[str]] = [["" for _ in range(n_cols)] for _ in range(n_rows)]
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
    return rows_data, n_cols


def star_cell_text(text: str, col_idx: int, star_col_idx: Optional[int]) -> str:
    """The matrix star-highlight rule: cells in the star column whose value
    is non-empty get a leading "★ " glyph (used for "recommended option"
    matrices). Logic identical to the pre-OUT2 inline rule in _add_matrix."""
    if star_col_idx is not None and col_idx == star_col_idx and str(text).strip():
        return "★ " + text
    return text


# Flag-word lookup for the contract-review matrix (SPEC OUT1 §4). Keyed on a
# normalized flag word so a skill passes plain 'ok' / 'warn' / 'bad' (or
# common synonyms) and gets the right palette KEY — the backend maps the key
# to its resolved brand tint (flag_ok / flag_warn / flag_bad hex).
FLAG_TINT_KEYS = {
    "ok": "flag_ok", "good": "flag_ok", "standard": "flag_ok",
    "green": "flag_ok", "favorable": "flag_ok", "pass": "flag_ok",
    "warn": "flag_warn", "warning": "flag_warn", "yellow": "flag_warn",
    "review": "flag_warn", "watch": "flag_warn", "caution": "flag_warn",
    "bad": "flag_bad", "red": "flag_bad", "flag": "flag_bad",
    "off-market": "flag_bad", "risk": "flag_bad", "push back": "flag_bad",
}

import re as _re  # noqa: E402


def flag_key_for(value: str) -> Optional[str]:
    """The brand-palette KEY ('flag_ok' / 'flag_warn' / 'flag_bad') for a flag
    cell's value, or None if it doesn't read as a flag word. Normalizes the
    cell text (strips glyphs / punctuation, takes the leading word) against
    FLAG_TINT_KEYS. Logic identical to the pre-OUT2 _flag_tint_for, minus the
    hex resolution (that stays with the backend's resolved palette)."""
    text = str(value or "").strip().lower()
    if not text:
        return None
    # Try the whole normalized phrase, then the leading word.
    cleaned = _re.sub(r"[^a-z\- ]+", "", text).strip()
    for key in (cleaned, cleaned.split(" ")[0] if cleaned else ""):
        if key in FLAG_TINT_KEYS:
            return FLAG_TINT_KEYS[key]
    return None


# ---------------------------------------------------------------------------
# HTML fragment backend (SPEC OUT2 §2a) — widget-surface markup matching
# shared/CHAT_ACTION_WIDGET.md styling.
#
# Markup classes are the widget's existing `.cr-counter-*` family (styled by
# chat_output_renderer._WIDGET_CSS when the fragment renders inside a chat
# widget). Standalone use (include_style=True) emits a scoped <style> whose
# colors/fonts resolve through brand.get_brand() (SPEC OUT2 §2c) — no
# palette constant is defined in this file.
# ---------------------------------------------------------------------------

def build_tile_band_html(
    tiles: List[Dict[str, str]],
    *,
    validate: bool = True,
    include_style: bool = False,
    brand: Optional[dict] = None,
    workspace_root: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """Render a stat-tile band as an HTML fragment.

    Markup is byte-identical to the widget's pre-OUT2 inline counter loop
    (`.cr-counter-grid` > `.cr-counter-card` > label + value), so migrating
    the widget's `counters` path onto this builder changes nothing on screen
    — labels/values unchanged (R4's counts["headline"]-verbatim rule keeps
    owning the numbers).

    Args:
      tiles: [{label, value}] — same shape both backends consume.
      validate: apply the component tile contract (drop-empty refusal, band
        cap). The widget's legacy `counters` path passes False — counters
        are R4-verbatim headline numbers where 0 is data and more than 5
        buckets is legitimate; the docx-parity `tiles` path uses True.
      include_style: emit a scoped <style> block so the fragment is
        self-contained outside a widget. Colors/fonts resolve through
        brand.get_brand() (explicit `brand` dict > workspace_root/org_id
        resolution > byte-stable defaults).
    """
    if validate:
        validate_tiles(tiles)
    parts: List[str] = []
    if include_style:
        parts.append(_tile_band_style(brand, workspace_root, org_id))
    parts.append('<div class="cr-counter-grid">')
    for t in tiles or []:
        label = _html.escape(str(t.get("label", "")))
        value = _html.escape(str(t.get("value", "")))
        parts.append(
            f'<div class="cr-counter-card">'
            f'<div class="cr-counter-label">{label}</div>'
            f'<div class="cr-counter-value">{value}</div>'
            f'</div>'
        )
    parts.append('</div>')
    return "".join(parts)


def build_two_col_table_html(
    rows: List[List[str]],
    *,
    headers: Optional[List[str]] = None,
    include_style: bool = False,
    brand: Optional[dict] = None,
    workspace_root: Optional[str] = None,
    org_id: Optional[str] = None,
) -> str:
    """Render a two-column table as an HTML fragment (label/value rows —
    the widget-surface sibling of the docx table primitive).

    Args:
      rows: non-empty list of 2-item lists/tuples ([label, value]).
      headers: optional 2-item header labels.
      include_style: as build_tile_band_html — self-contained styling
        resolved through brand.get_brand().

    Raises ValueError on an empty table, a row that is not a 2-item
    list/tuple, or headers that are not 2 items.
    """
    if not rows:
        raise ValueError("table 'rows' must be a non-empty list")
    for r in rows:
        if not isinstance(r, (list, tuple)) or len(r) != 2:
            raise ValueError(f"two-column table rows must be [label, value] pairs: {r!r}")
    if headers is not None and (not isinstance(headers, (list, tuple)) or len(headers) != 2):
        raise ValueError(f"two-column table headers must be 2 items: {headers!r}")

    parts: List[str] = []
    if include_style:
        parts.append(_two_col_table_style(brand, workspace_root, org_id))
    parts.append('<table class="cr-kv-table">')
    if headers:
        parts.append(
            '<thead><tr>'
            + "".join(f'<th class="cr-kv-th">{_html.escape(str(h))}</th>' for h in headers)
            + '</tr></thead>'
        )
    parts.append('<tbody>')
    for label, value in rows:
        parts.append(
            f'<tr class="cr-kv-row">'
            f'<td class="cr-kv-label">{_html.escape(str(label))}</td>'
            f'<td class="cr-kv-value">{_html.escape(str(value))}</td>'
            f'</tr>'
        )
    parts.append('</tbody></table>')
    return "".join(parts)


def build_table_html(
    rows: List[List[str]],
    *,
    headers: Optional[List[str]] = None,
    highlight_row_idx: Optional[int] = None,
) -> str:
    """Render an N-column table as an HTML fragment (SPEC OUT5 — the HTML
    sibling of brief_writer._add_table; same validator, same semantics).

    Markup-only: classes (`cr-table`, `cr-table-th`, `cr-table-row`,
    `cr-row-hl`, `cr-table-td`) are styled by the consuming surface (the
    premium brief template's CSS) — no palette constants here, per the
    components-library posture. `highlight_row_idx` marks the recommended row
    (`cr-row-hl`), matching the docx accent-tinted highlight. Zebra striping
    is a CSS concern (nth-child), not markup."""
    n_cols = validate_table(rows, headers, None)
    parts: List[str] = ['<table class="cr-table">']
    if headers:
        parts.append(
            '<thead><tr>'
            + "".join(f'<th class="cr-table-th">{_html.escape(str(h))}</th>' for h in headers)
            + '</tr></thead>'
        )
    parts.append('<tbody>')
    for i, row in enumerate(rows):
        cls = "cr-table-row cr-row-hl" if (
            highlight_row_idx is not None and i == highlight_row_idx
        ) else "cr-table-row"
        cells = "".join(
            f'<td class="cr-table-td">'
            f'{_html.escape(str(row[j]) if j < len(row) else "")}</td>'
            for j in range(n_cols)
        )
        parts.append(f'<tr class="{cls}">{cells}</tr>')
    parts.append('</tbody></table>')
    return "".join(parts)


def build_matrix_html(
    cells: Union[List[List[str]], Dict[tuple, str]],
    *,
    headers_row: Optional[List[str]] = None,
    headers_col: Optional[List[str]] = None,
    star_col_idx: Optional[int] = None,
    flag_col_idx: Optional[int] = None,
) -> str:
    """Render an N×M comparison matrix as an HTML fragment (SPEC OUT5 — the
    HTML sibling of brief_writer._add_matrix). Same normalization
    (normalize_matrix), same star-highlight rule (star_cell_text), same
    flag-word mapping (flag_key_for) — the flag KEY becomes a cell class
    (`cr-flag-ok` / `cr-flag-warn` / `cr-flag-bad`) so the consuming surface
    tints it, exactly as the docx backend maps the key to a brand tint hex."""
    rows_data, n_cols = normalize_matrix(cells, headers_row)
    parts: List[str] = ['<table class="cr-table cr-matrix">']
    if headers_row:
        head = ['<thead><tr>']
        if headers_col:
            head.append('<th class="cr-table-th"></th>')
        head.extend(
            f'<th class="cr-table-th">{_html.escape(str(h))}</th>' for h in headers_row
        )
        head.append('</tr></thead>')
        parts.append("".join(head))
    parts.append('<tbody>')
    for i, row_data in enumerate(rows_data):
        row_parts: List[str] = ['<tr class="cr-table-row">']
        if headers_col:
            label = headers_col[i] if i < len(headers_col) else ""
            row_parts.append(
                f'<th class="cr-matrix-rowhead">{_html.escape(str(label))}</th>'
            )
        for j in range(n_cols):
            text = str(row_data[j]) if j < len(row_data) else ""
            text = star_cell_text(text, j, star_col_idx)
            cls = "cr-table-td"
            if flag_col_idx is not None and j == flag_col_idx:
                key = flag_key_for(text)
                if key is not None:
                    cls += " cr-" + key.replace("_", "-")
            row_parts.append(f'<td class="{cls}">{_html.escape(text)}</td>')
        row_parts.append('</tr>')
        parts.append("".join(row_parts))
    parts.append('</tbody></table>')
    return "".join(parts)


def build_timeline_html(points: List[Dict[str, str]]) -> str:
    """Render the relationship-timeline strip as an HTML fragment (SPEC OUT5 —
    the HTML sibling of brief_writer._add_timeline). Same validator (>= 2
    points or refuse); the current point carries `cr-timeline-current` and the
    same '  — this meeting' marker text the docx strip renders."""
    validate_timeline(points)
    parts: List[str] = ['<ul class="cr-timeline">']
    for pt in points:
        is_current = bool(pt.get("current"))
        label = str(pt["label"]).strip()
        if is_current:
            label += "  — this meeting"
        cls = "cr-timeline-label cr-timeline-current" if is_current else "cr-timeline-label"
        parts.append(
            f'<li><span class="cr-timeline-when">{_html.escape(str(pt["date"]).strip())}</span>'
            f'<span class="{cls}">{_html.escape(label)}</span></li>'
        )
    parts.append('</ul>')
    return "".join(parts)


def _resolved(brand: Optional[dict], workspace_root: Optional[str],
              org_id: Optional[str]) -> dict:
    """The render theme for a standalone fragment — same precedence as the
    docx backend (explicit brand > workspace/org resolution > defaults)."""
    return brand if brand is not None else get_brand(workspace_root, org_id)


def _tile_band_style(brand: Optional[dict], workspace_root: Optional[str],
                     org_id: Optional[str]) -> str:
    """Scoped style for a standalone tile band. Every color/font comes from
    the resolved brand palette (SPEC OUT2 §2c)."""
    b = _resolved(brand, workspace_root, org_id)
    p = b["palette"]
    f = b["fonts"]
    return (
        "<style>"
        ".cr-counter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 8px; margin: 12px 0; }"
        f".cr-counter-card {{ background: #{p['tile_bg']}; border: 1px solid #{p['rule']}; border-radius: 6px; padding: 10px 12px; font-family: '{f['body']}', sans-serif; }}"
        f".cr-counter-label {{ font-size: 11px; color: #{p['accent']}; text-transform: uppercase; letter-spacing: 0.04em; margin-bottom: 4px; font-family: '{f['heading']}', serif; }}"
        f".cr-counter-value {{ font-size: 22px; color: #{p['heading']}; font-weight: 600; font-family: '{f['heading']}', serif; }}"
        "</style>"
    )


def _two_col_table_style(brand: Optional[dict], workspace_root: Optional[str],
                         org_id: Optional[str]) -> str:
    """Scoped style for a standalone two-column table — brand-resolved."""
    b = _resolved(brand, workspace_root, org_id)
    p = b["palette"]
    f = b["fonts"]
    return (
        "<style>"
        f".cr-kv-table {{ border-collapse: collapse; width: 100%; margin: 12px 0; font-family: '{f['body']}', sans-serif; font-size: 13px; color: #{p['ink']}; }}"
        f".cr-kv-th {{ text-align: left; background: #{p['table_header']}; color: #FFFFFF; padding: 6px 10px; font-family: '{f['heading']}', serif; font-size: 12px; }}"
        f".cr-kv-row:nth-child(even) {{ background: #{p['zebra']}; }}"
        f".cr-kv-label {{ padding: 6px 10px; color: #{p['muted']}; border-bottom: 1px solid #{p['rule']}; }}"
        f".cr-kv-value {{ padding: 6px 10px; border-bottom: 1px solid #{p['rule']}; }}"
        "</style>"
    )


__all__ = [
    "MAX_TILES_PER_BAND",
    "validate_tiles",
    "validate_timeline",
    "validate_table",
    "normalize_matrix",
    "star_cell_text",
    "FLAG_TINT_KEYS",
    "flag_key_for",
    "build_tile_band_html",
    "build_two_col_table_html",
    "build_table_html",
    "build_matrix_html",
    "build_timeline_html",
]
