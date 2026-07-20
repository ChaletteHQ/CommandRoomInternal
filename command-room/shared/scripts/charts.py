#!/usr/bin/env python3
"""Shared chart module — one owner, two backends (SPEC OUT3).

WHY THIS EXISTS
---------------
Before OUT3 the plugin had ZERO chart capability: the visual engine
(components.py — tiles / timeline / table / matrix) covers stats and
comparisons but nothing continuous. Numbers that WANT charts already flow
through code (operator-report trend context, board-pack KPIs-vs-targets and
period deltas, pipeline-tracker stage totals, value-receipt month-over-month)
— this module gives them a renderer without adding a runtime dependency.

THE DESIGN (SPEC OUT3 §3a)
--------------------------
`build_chart(kind, data, *, title=None, brand=None)` returns a SELF-CONTAINED
SVG STRING. Stdlib only — the SVG is hand-emitted; there is no matplotlib, no
JS runtime, no AntV code. AntV's contribution is adapted KNOWLEDGE (chart-type
selection heuristics, layout discipline — see `shared/CHART_SELECTION.md` and
the MIT notice in `THIRD_PARTY_NOTICES.md`), not their runtime.

Launch kinds and their validated data shapes:

    line        {"series": [{"label": str, "points": [{"x": str, "y": num}]}]}
                1-3 series, >= 2 points each — trends over time.
    bar         {"categories": [str], "series": [{"label": str, "values": [num]}]}
                exactly 1 series — ranking / single comparison.
    bar_grouped same shape, 2-4 series — comparison across a second dimension
                (e.g. current vs target).
    donut       {"slices": [{"label": str, "value": num > 0}]}
                2-6 slices — composition of one whole.
    waterfall   {"steps": [{"label": str, "delta": num}]}
                2-12 steps — a period delta walk; a "Net" bar is appended.

Any shape may carry an optional `"unit"` key: `"$"` renders as a prefix
("$1.2K"); any other short string renders as a suffix ("3.5h", "12%").

REFUSAL RULES (the components.py posture — drop-empty, refusal over empty
frames): a violated shape raises `ChartDataError` (a ValueError, same contract
as `validate_tiles`). A refused chart never renders an empty frame — the
CALLER falls back to its existing table/tile representation of the same
numbers. That fallback is structural: a `charts` entry never satisfies a
section's content requirement in brief_writer/premium_html, so a chart-only
section cannot exist.

BRAND (SPEC OUT1)
-----------------
Colors and fonts resolve ONLY through `brand.get_brand()` — pass the resolved
dict via `brand=` (per-org override honored by the caller's resolution), or
omit it for byte-stable defaults. No palette constant lives in this file; the
stray-palette guard (`tests/run_no_stray_palette_test.py`) keeps it that way.
Series/slice colors beyond the palette's named keys are COMPUTED tints of
brand colors, never new constants.

LEAK POSTURE
------------
Every string that reaches the SVG (title, labels) is scanned with
`docx_leak_scanner.scan_text_for_leaks` before render; findings raise
`ChartLeakError` — a chart can never paint a forbidden token into pixels the
docx post-render scan cannot see.

TWO BACKENDS, ONE SVG (SPEC OUT3 §3b)
-------------------------------------
- HTML surfaces (premium brief `.chart-slot`): the SVG embeds inline —
  self-contained, no asset server.
- docx / pptx: `rasterize_svg(svg) -> png_path | None`, a best-effort ladder
  mirroring `visual_gate.render_preview`'s posture EXACTLY: headless
  Chromium-family browser (Edge ships on stock Windows — zero installs) →
  inkscape → rsvg-convert → `None`. NEVER raises. `None` means no rasterizer
  on this machine — the calling section keeps its table/tile fallback,
  byte-identical to pre-OUT3. The ladder upgrades machines that can render;
  it never degrades ones that can't. Kill switch: `CR_CHART_RASTER=off`
  (or `0` / `skip`) forces the skipped path — CI and tests use this.

`try_chart_png(spec, brand=...)` is the render-chokepoint helper the docx and
pptx backends call: refusals, leak findings, and rasterizer absence ALL
collapse to `None` (leak findings additionally warn on stderr) — the
chokepoint never raises into a client render.

Stdlib only (brand.py and docx_leak_scanner.py are stdlib-only too; the
rasterizer ladder may SHELL OUT, never import-require).
"""
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from xml.sax.saxutils import escape as _xml_escape

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from brand import get_brand  # noqa: E402


# ---------------------------------------------------------------------------
# Caps — the machine copy of shared/CHART_SELECTION.md's pinned numbers
# (the DECK_GRAMMAR pattern: change one, change the other, same commit;
# tests/run_charts_test.py asserts the pins).
# ---------------------------------------------------------------------------

SELECTION_CAPS = {
    "max_line_series": 3,
    "min_line_points": 2,
    "max_grouped_series": 4,
    "min_donut_slices": 2,
    "max_donut_slices": 6,
    "min_waterfall_steps": 2,
    "max_waterfall_steps": 12,
}

SUPPORTED_KINDS = ("line", "bar", "bar_grouped", "donut", "waterfall")

# Canvas geometry — one fixed frame so every chart reads as one system.
_W, _H = 720, 400
_M_LEFT, _M_RIGHT, _M_TOP, _M_BOTTOM = 64, 24, 26, 48
_TITLE_H = 30  # extra top space when a title renders

# White paper background / white-on-dark contrast — the same contrast
# constant posture brief_writer and deck_writer carry (bare hex, no '#';
# the '#' joins only in the emitted SVG).
_PAPER = "FFFFFF"

_MAX_LABEL_CHARS = 16


class ChartDataError(ValueError):
    """The data shape violates the chart contract (same ValueError contract
    as components.validate_tiles). The caller's table/tile fallback stands —
    never render an empty frame."""


class ChartLeakError(RuntimeError):
    """Forbidden tokens found in chart text (pre-render). Nothing rendered —
    pixels are invisible to the docx post-render leak scan, so the scan runs
    here instead."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    """A finite number, or None. Bools are NOT numbers here."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return float(v)


def _fmt(v: float, unit: str = "") -> str:
    """Compact value label: 1234 -> '1.2K', 2500000 -> '2.5M'. `unit` '$' is
    a prefix; any other unit is a suffix ('3.5h'). Deterministic."""
    sign = "-" if v < 0 else ""
    a = abs(v)
    if a >= 1e9:
        s = f"{a / 1e9:.1f}".rstrip("0").rstrip(".") + "B"
    elif a >= 1e6:
        s = f"{a / 1e6:.1f}".rstrip("0").rstrip(".") + "M"
    elif a >= 1e3:
        s = f"{a / 1e3:.1f}".rstrip("0").rstrip(".") + "K"
    elif a == int(a):
        s = str(int(a))
    else:
        s = f"{a:.2f}".rstrip("0").rstrip(".")
    if unit == "$":
        return f"{sign}${s}"
    return f"{sign}{s}{unit}"


def _r(x: float) -> str:
    """Deterministic 2-dp coordinate."""
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _esc(s) -> str:
    return _xml_escape(str(s), {'"': "&quot;"})


def _trunc(s: str, n: int = _MAX_LABEL_CHARS) -> str:
    s = str(s)
    return s if len(s) <= n else s[: n - 1] + "…"


def _mix(hexstr: str, other: str, t: float) -> str:
    """Blend `hexstr` toward `other` by t in [0,1] — computed tints of brand
    colors, never new palette constants. Inputs/outputs are bare 6-hex."""
    a = str(hexstr).lstrip("#")
    b = str(other).lstrip("#")
    out = []
    for i in (0, 2, 4):
        va = int(a[i:i + 2], 16)
        vb = int(b[i:i + 2], 16)
        out.append(f"{round(va + (vb - va) * t):02X}")
    return "".join(out)


def _deepen(hexstr: str, rf: float, gf: float, bf: float) -> str:
    """Per-channel scale of a brand color (e.g. the flag_bad TINT deepened to
    a readable bar red) — a computed transform of a brand value, never a new
    constant. A client overriding the tint shifts the derived color with it."""
    a = str(hexstr).lstrip("#")
    out = []
    for i, f in zip((0, 2, 4), (rf, gf, bf)):
        v = int(a[i:i + 2], 16)
        out.append(f"{min(255, round(v * f)):02X}")
    return "".join(out)


def _series_colors(p: dict, n: int) -> List[str]:
    """Strong line/bar series colors from the brand palette: accent, heading,
    then a computed mid-tint of the two. Capped at 3-4 series by contract."""
    base = [
        p["accent"],
        p["heading"],
        _mix(p["accent"], _PAPER, 0.45),
        _mix(p["heading"], _PAPER, 0.45),
    ]
    return base[:n]


def _slice_colors(p: dict, n: int) -> List[str]:
    """Up to 6 composition colors — brand colors plus computed tints."""
    base = [
        p["accent"],
        p["heading"],
        _mix(p["accent"], _PAPER, 0.45),
        _mix(p["heading"], _PAPER, 0.45),
        p["muted"],
        _mix(p["muted"], _PAPER, 0.45),
    ]
    return base[:n]


def _nice_ticks(lo: float, hi: float, target: int = 5) -> List[float]:
    """4-6 'nice' axis ticks spanning [lo, hi] (always includes 0 when the
    range does). Deterministic."""
    lo = min(lo, 0.0)
    hi = max(hi, 0.0)
    if hi == lo:
        hi = lo + 1.0
    span = hi - lo
    raw = span / max(target - 1, 1)
    mag = 10 ** math.floor(math.log10(raw))
    step = next(
        (m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), 10 * mag
    )
    start = math.floor(lo / step) * step
    ticks = [round(start, 10)]
    # The last tick must clear the data max — a bar may never overflow the
    # frame (found on the first live waterfall render: max 52K, top tick 40K).
    while ticks[-1] < hi - step * 0.001:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def _leak_scan(strings: Sequence[str]) -> None:
    """Pre-render leak scan over every string that reaches the SVG. Raises
    ChartLeakError on findings. A missing scanner module (partial install)
    skips silently — same posture as brief_writer's lazy scan import."""
    try:
        from docx_leak_scanner import scan_text_for_leaks
    except ImportError:  # pragma: no cover — partial-install posture
        return
    findings = scan_text_for_leaks("\n".join(str(s) for s in strings if s))
    if findings:
        lines = [f"  [{f['name']}] {f['match']!r}" for f in findings[:10]]
        raise ChartLeakError(
            "Forbidden tokens in chart text (nothing rendered):\n"
            + "\n".join(lines)
        )


# ---------------------------------------------------------------------------
# Validation — pure, importable without rendering (deck plans validate shapes
# at plan time, python-pptx-free)
# ---------------------------------------------------------------------------

def _validate_line(data: dict) -> dict:
    series = data.get("series")
    if not isinstance(series, list) or not series:
        raise ChartDataError("line needs a non-empty 'series' list")
    if len(series) > SELECTION_CAPS["max_line_series"]:
        raise ChartDataError(
            f"line supports at most {SELECTION_CAPS['max_line_series']} series "
            f"(got {len(series)}) — more is overplotting; split the question."
        )
    norm_series = []
    any_signal = False
    for s in series:
        if not isinstance(s, dict) or not str(s.get("label") or "").strip():
            raise ChartDataError(f"each line series needs a 'label': {s!r}")
        pts = s.get("points")
        if not isinstance(pts, list) \
                or len(pts) < SELECTION_CAPS["min_line_points"]:
            raise ChartDataError(
                f"line series {s.get('label')!r} needs >= "
                f"{SELECTION_CAPS['min_line_points']} points; the caller drops "
                f"the chart below that (never render an empty frame)"
            )
        norm_pts = []
        for pt in pts:
            if not isinstance(pt, dict) or not str(pt.get("x") or "").strip():
                raise ChartDataError(f"line point needs 'x' and 'y': {pt!r}")
            y = _num(pt.get("y"))
            if y is None:
                raise ChartDataError(
                    f"line point 'y' must be a number: {pt!r} — drop unknown "
                    f"points at the caller, never chart a gap as zero"
                )
            if y != 0:
                any_signal = True
            norm_pts.append({"x": str(pt["x"]).strip(), "y": y})
        norm_series.append({"label": str(s["label"]).strip(), "points": norm_pts})
    if not any_signal:
        raise ChartDataError(
            "every line value is zero — an all-zero trend is an empty frame; "
            "the caller keeps its table/tile representation instead"
        )
    return {"series": norm_series, "unit": str(data.get("unit") or "")}


def _validate_bar(data: dict, grouped: bool) -> dict:
    cats = data.get("categories")
    if not isinstance(cats, list) or not cats \
            or not all(str(c or "").strip() for c in cats):
        raise ChartDataError("bar needs a non-empty 'categories' list of labels")
    series = data.get("series")
    if not isinstance(series, list) or not series:
        raise ChartDataError("bar needs a non-empty 'series' list")
    if grouped:
        if not (2 <= len(series) <= SELECTION_CAPS["max_grouped_series"]):
            raise ChartDataError(
                f"bar_grouped takes 2-{SELECTION_CAPS['max_grouped_series']} "
                f"series (got {len(series)}); one series is plain 'bar'"
            )
    elif len(series) != 1:
        raise ChartDataError(
            f"bar takes exactly 1 series (got {len(series)}); use "
            f"'bar_grouped' for a second dimension"
        )
    norm_series = []
    any_signal = False
    for s in series:
        if not isinstance(s, dict) or not str(s.get("label") or "").strip():
            raise ChartDataError(f"each bar series needs a 'label': {s!r}")
        vals = s.get("values")
        if not isinstance(vals, list) or len(vals) != len(cats):
            raise ChartDataError(
                f"series {s.get('label')!r} 'values' must match categories "
                f"({len(cats)}): got {vals!r}"
            )
        norm_vals = []
        for v in vals:
            nv = _num(v)
            if nv is None:
                raise ChartDataError(
                    f"bar value must be a number: {v!r} in series "
                    f"{s.get('label')!r} — drop unknown categories at the caller"
                )
            if nv != 0:
                any_signal = True
            norm_vals.append(nv)
        norm_series.append({"label": str(s["label"]).strip(), "values": norm_vals})
    if not any_signal:
        raise ChartDataError(
            "every bar value is zero — the caller keeps its table/tile "
            "representation instead of an empty frame"
        )
    return {
        "categories": [str(c).strip() for c in cats],
        "series": norm_series,
        "unit": str(data.get("unit") or ""),
    }


def _validate_donut(data: dict) -> dict:
    slices = data.get("slices")
    if not isinstance(slices, list):
        raise ChartDataError("donut needs a 'slices' list")
    if not (SELECTION_CAPS["min_donut_slices"] <= len(slices)
            <= SELECTION_CAPS["max_donut_slices"]):
        raise ChartDataError(
            f"donut takes {SELECTION_CAPS['min_donut_slices']}-"
            f"{SELECTION_CAPS['max_donut_slices']} slices (got {len(slices)}) "
            f"— fewer has no composition to show; more is unreadable "
            f"(group the tail into 'Other' at the caller)"
        )
    norm = []
    for sl in slices:
        if not isinstance(sl, dict) or not str(sl.get("label") or "").strip():
            raise ChartDataError(f"each slice needs a 'label': {sl!r}")
        v = _num(sl.get("value"))
        if v is None or v <= 0:
            raise ChartDataError(
                f"slice 'value' must be a positive number: {sl!r} — the "
                f"caller drops zero slices (drop-empty), never charts them"
            )
        norm.append({"label": str(sl["label"]).strip(), "value": v})
    return {"slices": norm, "unit": str(data.get("unit") or "")}


def _validate_waterfall(data: dict) -> dict:
    steps = data.get("steps")
    if not isinstance(steps, list):
        raise ChartDataError("waterfall needs a 'steps' list")
    if not (SELECTION_CAPS["min_waterfall_steps"] <= len(steps)
            <= SELECTION_CAPS["max_waterfall_steps"]):
        raise ChartDataError(
            f"waterfall takes {SELECTION_CAPS['min_waterfall_steps']}-"
            f"{SELECTION_CAPS['max_waterfall_steps']} steps (got {len(steps)})"
        )
    norm = []
    any_signal = False
    for st in steps:
        if not isinstance(st, dict) or not str(st.get("label") or "").strip():
            raise ChartDataError(f"each step needs a 'label': {st!r}")
        d = _num(st.get("delta"))
        if d is None:
            raise ChartDataError(f"step 'delta' must be a number: {st!r}")
        if d != 0:
            any_signal = True
        norm.append({"label": str(st["label"]).strip(), "delta": d})
    if not any_signal:
        raise ChartDataError(
            "every waterfall delta is zero — there is no walk to show; the "
            "caller keeps its table representation instead"
        )
    return {"steps": norm, "unit": str(data.get("unit") or "")}


_VALIDATORS = {
    "line": _validate_line,
    "bar": lambda d: _validate_bar(d, grouped=False),
    "bar_grouped": lambda d: _validate_bar(d, grouped=True),
    "donut": _validate_donut,
    "waterfall": _validate_waterfall,
}


def validate_chart(spec: dict) -> None:
    """Validate a {kind, data, title?} chart spec without rendering (pure —
    what deck plans call at plan time). Raises ChartDataError / ValueError."""
    if not isinstance(spec, dict):
        raise ChartDataError(f"chart spec must be a dict: {spec!r}")
    kind = spec.get("kind")
    if kind not in SUPPORTED_KINDS:
        raise ChartDataError(
            f"kind must be one of {list(SUPPORTED_KINDS)}, got {kind!r}"
        )
    data = spec.get("data")
    if not isinstance(data, dict):
        raise ChartDataError(f"chart spec needs a 'data' dict: {spec!r}")
    _VALIDATORS[kind](data)


def chart_strings(spec: dict) -> List[str]:
    """Every text string in a chart spec that could reach pixels — the leak-
    scan input (deck plans fold this into _plan_strings)."""
    out: List[str] = []
    if not isinstance(spec, dict):
        return out
    if spec.get("title"):
        out.append(str(spec["title"]))
    data = spec.get("data") if isinstance(spec.get("data"), dict) else {}
    # 'unit' renders into EVERY axis tick and value label — it is chart text,
    # not a number, and must join the pre-render scan like any other string.
    if data.get("unit"):
        out.append(str(data["unit"]))
    for s in data.get("series") or []:
        if isinstance(s, dict):
            if s.get("label"):
                out.append(str(s["label"]))
            for pt in s.get("points") or []:
                if isinstance(pt, dict) and pt.get("x"):
                    out.append(str(pt["x"]))
    out.extend(str(c) for c in (data.get("categories") or []))
    for key in ("slices", "steps"):
        for item in data.get(key) or []:
            if isinstance(item, dict) and item.get("label"):
                out.append(str(item["label"]))
    return out


# ---------------------------------------------------------------------------
# SVG emission
# ---------------------------------------------------------------------------

def _svg_open(title: Optional[str], p: dict, fonts: dict) -> Tuple[List[str], float]:
    """Shared frame: root element, paper background, optional title. Returns
    (parts, plot_top)."""
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_W}" height="{_H}" '
        f'viewBox="0 0 {_W} {_H}" role="img">',
        f'<rect x="0" y="0" width="{_W}" height="{_H}" fill="#{_PAPER}"/>',
    ]
    top = _M_TOP
    if title:
        parts.append(f"<title>{_esc(title)}</title>")
        parts.append(
            f'<text x="{_M_LEFT}" y="{_M_TOP - 4}" '
            f'font-family="{_esc(fonts["heading"])}, Georgia, serif" '
            f'font-size="15" font-weight="600" fill="#{p["heading"]}">'
            f"{_esc(title)}</text>"
        )
        top += _TITLE_H
    return parts, float(top)


def _text(x: float, y: float, s: str, *, size: int, fill: str, fonts: dict,
          anchor: str = "start", weight: str = "normal") -> str:
    return (
        f'<text x="{_r(x)}" y="{_r(y)}" text-anchor="{anchor}" '
        f'font-family="{_esc(fonts["body"])}, Calibri, sans-serif" '
        f'font-size="{size}" font-weight="{weight}" fill="#{fill}">'
        f"{_esc(s)}</text>"
    )


def _y_axis(parts: List[str], ticks: List[float], y_of, plot_left: float,
            plot_right: float, unit: str, p: dict, fonts: dict) -> None:
    for t in ticks:
        y = y_of(t)
        parts.append(
            f'<line x1="{_r(plot_left)}" y1="{_r(y)}" x2="{_r(plot_right)}" '
            f'y2="{_r(y)}" stroke="#{p["rule"]}" stroke-width="1"/>'
        )
        parts.append(_text(plot_left - 8, y + 4, _fmt(t, unit), size=11,
                           fill=p["muted"], fonts=fonts, anchor="end"))


def _legend(parts: List[str], labels: List[str], colors: List[str],
            top: float, p: dict, fonts: dict) -> None:
    """Top-right swatch legend (multi-series charts only)."""
    x = _W - _M_RIGHT
    for label, color in zip(reversed(labels), reversed(colors)):
        label = _trunc(label)
        w = len(label) * 6.6 + 18
        x -= w
        parts.append(
            f'<rect x="{_r(x)}" y="{_r(top - 9)}" width="10" height="10" '
            f'rx="2" fill="#{color}"/>'
        )
        parts.append(_text(x + 14, top, label, size=11, fill=p["ink"],
                           fonts=fonts))
        x -= 14


def _x_band_labels(parts: List[str], labels: List[str], band_of, baseline: float,
                   p: dict, fonts: dict) -> None:
    """Centered x labels, thinned when crowded (axis discipline: never
    overlap; show every k-th)."""
    n = len(labels)
    est_w = max(len(_trunc(l)) for l in labels) * 6.6
    band_w = band_of(1)[0] - band_of(0)[0] if n > 1 else _W
    k = max(1, math.ceil(est_w / max(band_w, 1)))
    for i, label in enumerate(labels):
        if i % k and i != n - 1:
            continue
        cx, cw = band_of(i)
        parts.append(_text(cx + cw / 2, baseline + 16, _trunc(label), size=11,
                           fill=p["muted"], fonts=fonts, anchor="middle"))


def _render_line(norm: dict, title: Optional[str], p: dict, fonts: dict) -> str:
    series = norm["series"]
    unit = norm["unit"]
    parts, top = _svg_open(title, p, fonts)
    plot_left, plot_right = float(_M_LEFT), float(_W - _M_RIGHT)
    plot_bottom = float(_H - _M_BOTTOM)

    if len(series) > 1:
        _legend(parts, [s["label"] for s in series],
                _series_colors(p, len(series)), top + 2, p, fonts)
        top += 18

    # Ordinal x domain: distinct x labels in first-seen order across series.
    x_labels: List[str] = []
    for s in series:
        for pt in s["points"]:
            if pt["x"] not in x_labels:
                x_labels.append(pt["x"])
    n = len(x_labels)
    x_of = {
        lab: plot_left + (plot_right - plot_left) * (i / (n - 1) if n > 1 else 0.5)
        for i, lab in enumerate(x_labels)
    }

    all_y = [pt["y"] for s in series for pt in s["points"]]
    ticks = _nice_ticks(min(all_y), max(all_y))
    lo, hi = ticks[0], ticks[-1]

    def y_of(v: float) -> float:
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - top)

    _y_axis(parts, ticks, y_of, plot_left, plot_right, unit, p, fonts)

    def band_of(i: int):
        step = (plot_right - plot_left) / (n - 1) if n > 1 else (plot_right - plot_left)
        return (plot_left + i * step - step / 2, step)

    _x_band_labels(parts, x_labels, band_of, plot_bottom, p, fonts)

    colors = _series_colors(p, len(series))
    for s, color in zip(series, colors):
        pts = [(x_of[pt["x"]], y_of(pt["y"])) for pt in s["points"]]
        path = " ".join(f"{_r(x)},{_r(y)}" for x, y in pts)
        parts.append(
            f'<polyline points="{path}" fill="none" stroke="#{color}" '
            f'stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>'
        )
        for x, y in pts:
            parts.append(
                f'<circle cx="{_r(x)}" cy="{_r(y)}" r="3.2" fill="#{color}" '
                f'stroke="#{_PAPER}" stroke-width="1.5"/>'
            )
    # Single-series trend: label the last point (the one message per chart).
    if len(series) == 1:
        last = series[0]["points"][-1]
        parts.append(_text(x_of[last["x"]], y_of(last["y"]) - 10,
                           _fmt(last["y"], unit), size=12, fill=p["heading"],
                           fonts=fonts, anchor="middle", weight="600"))
    parts.append("</svg>")
    return "".join(parts)


def _render_bar(norm: dict, title: Optional[str], p: dict, fonts: dict) -> str:
    cats = norm["categories"]
    series = norm["series"]
    unit = norm["unit"]
    grouped = len(series) > 1
    parts, top = _svg_open(title, p, fonts)
    plot_left, plot_right = float(_M_LEFT), float(_W - _M_RIGHT)
    plot_bottom = float(_H - _M_BOTTOM)

    if grouped:
        _legend(parts, [s["label"] for s in series],
                _series_colors(p, len(series)), top + 2, p, fonts)
        top += 18

    all_v = [v for s in series for v in s["values"]]
    ticks = _nice_ticks(min(all_v), max(all_v))
    lo, hi = ticks[0], ticks[-1]

    def y_of(v: float) -> float:
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - top)

    _y_axis(parts, ticks, y_of, plot_left, plot_right, unit, p, fonts)

    n = len(cats)
    band = (plot_right - plot_left) / n
    inner = band * 0.62
    bar_w = inner / len(series)
    colors = _series_colors(p, len(series))
    zero_y = y_of(0.0)

    def band_of(i: int):
        return (plot_left + i * band, band)

    for ci in range(n):
        x0 = plot_left + ci * band + (band - inner) / 2
        for si, (s, color) in enumerate(zip(series, colors)):
            v = s["values"][ci]
            y1, y2 = sorted((y_of(v), zero_y))
            h = max(y2 - y1, 0.5) if v != 0 else 0.0
            if h:
                parts.append(
                    f'<rect x="{_r(x0 + si * bar_w)}" y="{_r(y1)}" '
                    f'width="{_r(max(bar_w - 2, 1))}" height="{_r(h)}" '
                    f'rx="2" fill="#{color}"/>'
                )
            if not grouped:
                parts.append(_text(
                    x0 + si * bar_w + (bar_w - 2) / 2,
                    (y1 - 6) if v >= 0 else (y2 + 14),
                    _fmt(v, unit), size=11, fill=p["heading"], fonts=fonts,
                    anchor="middle", weight="600"))
    _x_band_labels(parts, cats, band_of, plot_bottom, p, fonts)
    parts.append("</svg>")
    return "".join(parts)


def _render_donut(norm: dict, title: Optional[str], p: dict, fonts: dict) -> str:
    slices = norm["slices"]
    unit = norm["unit"]
    parts, top = _svg_open(title, p, fonts)
    total = sum(sl["value"] for sl in slices)
    cy = top + (_H - _M_BOTTOM / 2 - top) / 2
    cx = _M_LEFT + 150.0
    r_out, r_in = 118.0, 72.0
    colors = _slice_colors(p, len(slices))

    def pt(radius: float, ang: float) -> Tuple[float, float]:
        return (cx + radius * math.cos(ang), cy + radius * math.sin(ang))

    ang = -math.pi / 2  # 12 o'clock, clockwise
    for sl, color in zip(slices, colors):
        frac = sl["value"] / total
        # Hairline gap between slices keeps adjacency readable.
        a0 = ang + 0.015
        a1 = ang + frac * 2 * math.pi - 0.015
        a1 = max(a1, a0 + 0.002)
        large = 1 if (a1 - a0) > math.pi else 0
        x0o, y0o = pt(r_out, a0)
        x1o, y1o = pt(r_out, a1)
        x1i, y1i = pt(r_in, a1)
        x0i, y0i = pt(r_in, a0)
        parts.append(
            f'<path d="M {_r(x0o)} {_r(y0o)} '
            f'A {r_out} {r_out} 0 {large} 1 {_r(x1o)} {_r(y1o)} '
            f'L {_r(x1i)} {_r(y1i)} '
            f'A {r_in} {r_in} 0 {large} 0 {_r(x0i)} {_r(y0i)} Z" '
            f'fill="#{color}"/>'
        )
        ang += frac * 2 * math.pi

    # Center: the whole this composes.
    parts.append(_text(cx, cy - 2, _fmt(total, unit), size=22,
                       fill=p["heading"], fonts=fonts, anchor="middle",
                       weight="600"))
    parts.append(_text(cx, cy + 18, "TOTAL", size=10, fill=p["muted"],
                       fonts=fonts, anchor="middle"))

    # Right-side legend: swatch · label · value (share%).
    lx = cx + r_out + 46
    ly = cy - (len(slices) - 1) * 13.0
    for sl, color in zip(slices, colors):
        parts.append(
            f'<rect x="{_r(lx)}" y="{_r(ly - 9)}" width="10" height="10" '
            f'rx="2" fill="#{color}"/>'
        )
        share = round(100 * sl["value"] / total)
        parts.append(_text(
            lx + 16, ly,
            f"{_trunc(sl['label'], 22)} — {_fmt(sl['value'], unit)} ({share}%)",
            size=12, fill=p["ink"], fonts=fonts))
        ly += 26
    parts.append("</svg>")
    return "".join(parts)


def _render_waterfall(norm: dict, title: Optional[str], p: dict,
                      fonts: dict) -> str:
    steps = norm["steps"]
    unit = norm["unit"]
    parts, top = _svg_open(title, p, fonts)
    plot_left, plot_right = float(_M_LEFT), float(_W - _M_RIGHT)
    plot_bottom = float(_H - _M_BOTTOM)

    # The walk: each step floats from the running total; a Net bar closes it.
    cum = [0.0]
    for st in steps:
        cum.append(cum[-1] + st["delta"])
    ticks = _nice_ticks(min(cum), max(cum))
    lo, hi = ticks[0], ticks[-1]

    def y_of(v: float) -> float:
        return plot_bottom - (v - lo) / (hi - lo) * (plot_bottom - top)

    _y_axis(parts, ticks, y_of, plot_left, plot_right, unit, p, fonts)

    labels = [st["label"] for st in steps] + ["Net"]
    n = len(labels)
    band = (plot_right - plot_left) / n
    bar_w = band * 0.62
    pos_c = p["accent"]
    neg_c = _deepen(p["flag_bad"], 0.75, 0.35, 0.35)  # flag_bad tint → bar red
    net_c = p["heading"]

    def band_of(i: int):
        return (plot_left + i * band, band)

    prev_edge_x = None
    prev_level = 0.0
    for i, st in enumerate(steps):
        x0 = plot_left + i * band + (band - bar_w) / 2
        v0, v1 = cum[i], cum[i + 1]
        y1, y2 = sorted((y_of(v0), y_of(v1)))
        color = pos_c if st["delta"] >= 0 else neg_c
        parts.append(
            f'<rect x="{_r(x0)}" y="{_r(y1)}" width="{_r(bar_w)}" '
            f'height="{_r(max(y2 - y1, 1.0))}" rx="2" fill="#{color}"/>'
        )
        if prev_edge_x is not None:
            parts.append(
                f'<line x1="{_r(prev_edge_x)}" y1="{_r(y_of(prev_level))}" '
                f'x2="{_r(x0)}" y2="{_r(y_of(v0))}" stroke="#{p["muted"]}" '
                f'stroke-width="1" stroke-dasharray="3,3"/>'
            )
        sign = "+" if st["delta"] >= 0 else ""
        parts.append(_text(x0 + bar_w / 2, y1 - 6,
                           sign + _fmt(st["delta"], unit), size=11,
                           fill=p["heading"], fonts=fonts, anchor="middle",
                           weight="600"))
        prev_edge_x = x0 + bar_w
        prev_level = v1

    # Net bar: 0 -> total, in heading color.
    x0 = plot_left + len(steps) * band + (band - bar_w) / 2
    total = cum[-1]
    y1, y2 = sorted((y_of(0.0), y_of(total)))
    parts.append(
        f'<rect x="{_r(x0)}" y="{_r(y1)}" width="{_r(bar_w)}" '
        f'height="{_r(max(y2 - y1, 1.0))}" rx="2" fill="#{net_c}"/>'
    )
    parts.append(
        f'<line x1="{_r(prev_edge_x)}" y1="{_r(y_of(prev_level))}" '
        f'x2="{_r(x0)}" y2="{_r(y_of(total))}" stroke="#{p["muted"]}" '
        f'stroke-width="1" stroke-dasharray="3,3"/>'
    )
    parts.append(_text(x0 + bar_w / 2, y1 - 6, _fmt(total, unit), size=11,
                       fill=p["heading"], fonts=fonts, anchor="middle",
                       weight="600"))
    parts.append(
        f'<line x1="{_r(plot_left)}" y1="{_r(y_of(0.0))}" '
        f'x2="{_r(plot_right)}" y2="{_r(y_of(0.0))}" '
        f'stroke="#{p["muted"]}" stroke-width="1"/>'
    )
    _x_band_labels(parts, labels, band_of, plot_bottom, p, fonts)
    parts.append("</svg>")
    return "".join(parts)


_RENDERERS = {
    "line": _render_line,
    "bar": _render_bar,
    "bar_grouped": _render_bar,
    "donut": _render_donut,
    "waterfall": _render_waterfall,
}


def build_chart(kind: str, data: dict, *, title: Optional[str] = None,
                brand: Optional[dict] = None) -> str:
    """Render a chart to a self-contained SVG string (SPEC OUT3 §3a).

    Args:
      kind: one of SUPPORTED_KINDS ('line' / 'bar' / 'bar_grouped' / 'donut'
        / 'waterfall').
      data: the kind's validated shape (module docstring). Optional 'unit'
        key: '$' prefixes values; any other short string suffixes them.
      title: optional chart title (rendered top-left, brand heading style).
      brand: a RESOLVED brand dict (the caller's get_brand(workspace_root,
        org_id) result — per-org override honored). None = byte-stable
        DEFAULT_BRAND, same precedence contract as components.py.

    Returns the SVG string. Deterministic: same input + same brand → the
    same bytes, every call.

    Raises:
      ChartDataError (a ValueError) on a shape/refusal violation — the caller
        falls back to its table/tile representation of the same numbers.
      ChartLeakError when a forbidden token appears in chart text.
    """
    if kind not in SUPPORTED_KINDS:
        raise ChartDataError(
            f"kind must be one of {list(SUPPORTED_KINDS)}, got {kind!r}"
        )
    if not isinstance(data, dict):
        raise ChartDataError(f"'data' must be a dict, got {type(data).__name__}")
    norm = _VALIDATORS[kind](data)
    _leak_scan(chart_strings({"kind": kind, "data": data, "title": title}))
    resolved = brand if brand is not None else get_brand()
    return _RENDERERS[kind](norm, (str(title).strip() or None) if title else None,
                            resolved["palette"], resolved["fonts"])


# ---------------------------------------------------------------------------
# SVG -> PNG rasterizer ladder (SPEC OUT3 §3b — the visual_gate posture:
# best-effort, NEVER raises, None = no rasterizer = caller's fallback stands)
# ---------------------------------------------------------------------------

_SUBPROCESS_TIMEOUT_S = 60
_RASTER_SCALE = 2  # 2x the SVG's nominal size — crisp in print

# Known install locations checked after PATH (Edge/Chrome don't register on
# PATH by default on Windows) — mirrors visual_gate._BROWSER_KNOWN_PATHS.
_BROWSER_CANDIDATES = (
    "msedge", "msedge.exe", "chrome", "chrome.exe",
    "google-chrome", "chromium", "chromium-browser",
)
_BROWSER_KNOWN_PATHS = (
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)

_SVG_SIZE_RE = re.compile(r'<svg[^>]*\swidth="(\d+)"[^>]*\sheight="(\d+)"')


def _find_headless_browser() -> Optional[str]:
    for name in _BROWSER_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    for path in _BROWSER_KNOWN_PATHS:
        if os.path.isfile(path):
            return path
    return None


def _svg_dims(svg: str) -> Tuple[int, int]:
    m = _SVG_SIZE_RE.search(svg)
    if m:
        return int(m.group(1)), int(m.group(2))
    return _W, _H


def _svg_to_png_browser(svg_path: str, out_png: str, w: int, h: int) -> Optional[str]:
    """Rung 1 — headless Chromium-family screenshot (Edge ships on stock
    Windows; zero installs — the same load-bearing rung visual_gate's HTML
    path uses). Never raises."""
    browser = _find_headless_browser()
    if not browser:
        return None
    profile_dir = None
    try:
        import time
        profile_dir = tempfile.mkdtemp(prefix="cr_chart_profile_")
        subprocess.run(
            [
                browser, "--headless", "--disable-gpu", "--hide-scrollbars",
                "--no-first-run", f"--user-data-dir={profile_dir}",
                f"--screenshot={out_png}",
                f"--window-size={w},{h}",
                f"--force-device-scale-factor={_RASTER_SCALE}",
                Path(svg_path).resolve().as_uri(),
            ],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        # The Windows msedge.exe launcher exits before the real browser
        # process flushes the capture — poll briefly (the visual_gate lesson:
        # rc 0, file lands ~1s later).
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if os.path.isfile(out_png) and os.path.getsize(out_png) > 0:
                return out_png
            time.sleep(0.25)
        return None
    except Exception:
        return None
    finally:
        # The throwaway profile is pure waste once the capture lands or fails
        # (the G15 review's Windows temp-leak class).
        if profile_dir:
            shutil.rmtree(profile_dir, ignore_errors=True)


def _svg_to_png_inkscape(svg_path: str, out_png: str, w: int, h: int) -> Optional[str]:
    """Rung 2 — inkscape CLI, if on PATH. Never raises."""
    tool = shutil.which("inkscape")
    if not tool:
        return None
    try:
        subprocess.run(
            [tool, str(svg_path), "--export-type=png",
             f"--export-filename={out_png}", "-w", str(w * _RASTER_SCALE)],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        return out_png if os.path.isfile(out_png) and os.path.getsize(out_png) > 0 else None
    except Exception:
        return None


def _svg_to_png_rsvg(svg_path: str, out_png: str, w: int, h: int) -> Optional[str]:
    """Rung 3 — rsvg-convert (common on Linux), if on PATH. Never raises."""
    tool = shutil.which("rsvg-convert")
    if not tool:
        return None
    try:
        subprocess.run(
            [tool, "-w", str(w * _RASTER_SCALE), "-o", out_png, str(svg_path)],
            check=True, capture_output=True, timeout=_SUBPROCESS_TIMEOUT_S,
        )
        return out_png if os.path.isfile(out_png) and os.path.getsize(out_png) > 0 else None
    except Exception:
        return None


# Module-level so tests can monkeypatch the ladder empty (the CI-shaped
# "no rasterizer exists" case) without depending on the host machine —
# the visual_gate._DOCX_TO_PDF_LADDER pattern.
_SVG_TO_PNG_LADDER = (
    _svg_to_png_browser,
    _svg_to_png_inkscape,
    _svg_to_png_rsvg,
)


def rasterize_svg(svg: str) -> Optional[str]:
    """Best-effort SVG -> PNG. Returns the PNG path (in a fresh session temp
    dir — previews and embeds are ephemeral render input, never workspace
    files), or `None` when no rasterizer is available / anything at all goes
    wrong. NEVER raises into the caller — `None` is the universal "keep the
    table/tile fallback" answer and MUST leave the calling render
    byte-identical to pre-OUT3 (visual_gate.render_preview's exact contract).

    Kill switch: `CR_CHART_RASTER=off` (or `0` / `skip`) forces `None` —
    CI and tests use this for determinism.
    """
    try:
        if os.environ.get("CR_CHART_RASTER", "").strip().lower() in ("off", "0", "skip"):
            return None
        if not svg or "<svg" not in svg:
            return None
        out_dir = tempfile.mkdtemp(prefix="cr_chart_")
        svg_path = os.path.join(out_dir, "chart.svg")
        with open(svg_path, "w", encoding="utf-8") as f:
            f.write(svg)
        out_png = os.path.join(out_dir, "chart.png")
        w, h = _svg_dims(svg)
        for rung in _SVG_TO_PNG_LADDER:
            png = rung(svg_path, out_png, w, h)
            if png:
                return png
        return None
    except Exception:
        return None


def try_chart_png(spec: dict, *, brand: Optional[dict] = None) -> Optional[str]:
    """The render-chokepoint helper (docx + pptx backends): build the chart
    from a {kind, data, title?} spec and rasterize it. Returns the PNG path,
    or `None` on ANY refusal / leak finding / missing rasterizer — never
    raises into a client render (a leak finding warns on stderr; a shape
    refusal is silent — the caller's fallback is the designed path).
    """
    try:
        if not isinstance(spec, dict):
            return None
        svg = build_chart(
            spec.get("kind"), spec.get("data"),
            title=spec.get("title"), brand=brand,
        )
        return rasterize_svg(svg)
    except ChartLeakError as e:
        print(f"[charts] chart dropped (leak scan): {e}", file=sys.stderr)
        return None
    except ChartDataError:
        return None  # refusal — the table/tile fallback stands by design
    except Exception as e:  # pragma: no cover — chokepoint never raises
        print(f"[charts] chart dropped ({type(e).__name__}): {e}", file=sys.stderr)
        return None


__all__ = [
    "SUPPORTED_KINDS",
    "SELECTION_CAPS",
    "ChartDataError",
    "ChartLeakError",
    "build_chart",
    "validate_chart",
    "chart_strings",
    "rasterize_svg",
    "try_chart_png",
]
