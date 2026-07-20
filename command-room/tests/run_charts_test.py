#!/usr/bin/env python3
"""SPEC OUT3 — charts.py + consumer wiring tests.

The load-bearing assertions are the NEGATIVE ones (the visual_gate pattern):

  1. With NO rasterizer (ladder monkeypatched empty — CI has none, that IS
     the test), every charted render is BYTE-IDENTICAL to the same render
     with no charts key: docx, pptx. The chart layer upgrades machines that
     can render; it never degrades ones that can't.
  2. A refused shape (one-point trend, all-zero series, single donut slice)
     raises ChartDataError from build_chart and collapses to None at the
     try_chart_png chokepoint — never an empty frame, the table stands.
  3. A 'charts'-only section is REFUSED by both document backends — the
     fallback representation is structural, not a convention.

Plus the positive path: SVG well-formed per kind (xml.etree), brand palette
resolution incl. org override, golden determinism, the leak scan on chart
text, the SELECTION_CAPS prose pins, and the computation helpers
(value_receipt.build_trend_chart / pipeline_math.stage_mix) whose numbers
must be verbatim from their inputs.
"""
from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import charts  # noqa: E402
from ooxml_payload_lib import zip_payload_identical as _zip_payload_identical  # noqa: E402

_failures = []


def check(name, cond, detail=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}" + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        _failures.append(name)


# Deterministic fixtures — placeholder org/deal names only (org-name gotcha).
LINE_DATA = {
    "series": [{"label": "Hours returned", "points": [
        {"x": "May 2026", "y": 12.0}, {"x": "Jun 2026", "y": 18.5},
        {"x": "Jul 2026", "y": 9.0},
    ]}],
    "unit": "h",
}
BAR_DATA = {
    "categories": ["Sample Co", "Placeholder LLC", "Example Inc"],
    "series": [{"label": "Revenue at stake", "values": [52000, 30000, 12000]}],
    "unit": "$",
}
GROUPED_DATA = {
    "categories": ["MRR", "Pipeline"],
    "series": [
        {"label": "Current", "values": [420000, 910000]},
        {"label": "Target", "values": [450000, 800000]},
    ],
    "unit": "$",
}
DONUT_DATA = {
    "slices": [
        {"label": "Lead", "value": 4}, {"label": "Qualified", "value": 3},
        {"label": "Proposal sent", "value": 2}, {"label": "Negotiating", "value": 1},
    ],
}
WATERFALL_DATA = {
    "steps": [
        {"label": "Won Sample Deal", "delta": 52000},
        {"label": "Lost Placeholder Co", "delta": -30000},
        {"label": "Won Example LLC", "delta": 12000},
    ],
    "unit": "$",
}
ALL_KINDS = [
    ("line", LINE_DATA), ("bar", BAR_DATA), ("bar_grouped", GROUPED_DATA),
    ("donut", DONUT_DATA), ("waterfall", WATERFALL_DATA),
]

GOLDEN = Path(__file__).resolve().parent / "golden" / "chart_line_golden.svg"


def _no_raster(fn):
    """Run fn with the rasterizer ladder empty (the CI-shaped case)."""
    original = charts._SVG_TO_PNG_LADDER
    try:
        charts._SVG_TO_PNG_LADDER = ()
        return fn()
    finally:
        charts._SVG_TO_PNG_LADDER = original


def test_svg_well_formed_every_kind():
    for kind, data in ALL_KINDS:
        svg = charts.build_chart(kind, data, title=f"{kind} fixture")
        ok = True
        try:
            ET.fromstring(svg)
        except ET.ParseError as e:
            ok = False
            print("   parse error:", e)
        check(f"{kind}: SVG parses (xml.etree)", ok)
        check(f"{kind}: self-contained single root", svg.startswith("<svg")
              and svg.endswith("</svg>") and "http://" not in svg.replace(
                  "http://www.w3.org/2000/svg", ""))


def test_refusal_rules():
    cases = [
        ("line one-point series",
         "line", {"series": [{"label": "t", "points": [{"x": "a", "y": 1}]}]}),
        ("line all-zero",
         "line", {"series": [{"label": "t", "points": [
             {"x": "a", "y": 0}, {"x": "b", "y": 0}]}]}),
        ("line too many series",
         "line", {"series": [{"label": str(i), "points": [
             {"x": "a", "y": 1}, {"x": "b", "y": 2}]} for i in range(4)]}),
        ("line None y",
         "line", {"series": [{"label": "t", "points": [
             {"x": "a", "y": None}, {"x": "b", "y": 2}]}]}),
        ("bar values/categories mismatch",
         "bar", {"categories": ["a", "b"],
                 "series": [{"label": "s", "values": [1]}]}),
        ("bar all-zero",
         "bar", {"categories": ["a", "b"],
                 "series": [{"label": "s", "values": [0, 0]}]}),
        ("bar with 2 series (must be bar_grouped)",
         "bar", {"categories": ["a"], "series": [
             {"label": "s", "values": [1]}, {"label": "t", "values": [2]}]}),
        ("bar_grouped with 1 series (must be bar)",
         "bar_grouped", {"categories": ["a"],
                         "series": [{"label": "s", "values": [1]}]}),
        ("donut single slice",
         "donut", {"slices": [{"label": "only", "value": 5}]}),
        ("donut zero-value slice",
         "donut", {"slices": [{"label": "a", "value": 5},
                              {"label": "b", "value": 0}]}),
        ("donut over 6 slices",
         "donut", {"slices": [{"label": str(i), "value": 1} for i in range(7)]}),
        ("waterfall single step",
         "waterfall", {"steps": [{"label": "a", "delta": 5}]}),
        ("waterfall all-zero",
         "waterfall", {"steps": [{"label": "a", "delta": 0},
                                 {"label": "b", "delta": 0}]}),
        ("unknown kind", "sparkline", {"series": []}),
        ("non-dict data", "line", None),
    ]
    for name, kind, data in cases:
        try:
            charts.build_chart(kind, data)
            check(f"refusal: {name}", False, "did not raise")
        except charts.ChartDataError:
            check(f"refusal: {name}", True)
        except Exception as e:
            check(f"refusal: {name}", False, f"wrong exception {type(e).__name__}")
        # The chokepoint collapses the same refusal to None, never raises.
        check(f"chokepoint None: {name}",
              charts.try_chart_png({"kind": kind, "data": data}) is None)


def test_refusal_is_a_valueerror():
    """Same contract as components.validate_tiles — callers may catch ValueError."""
    check("ChartDataError is a ValueError",
          issubclass(charts.ChartDataError, ValueError))


def test_brand_resolution_and_org_override():
    from brand import get_brand, DEFAULT_BRAND
    svg = charts.build_chart("bar", BAR_DATA)
    check("default accent paints the bars",
          f'#{DEFAULT_BRAND["palette"]["accent"]}' in svg)
    check("default heading font on the title",
          DEFAULT_BRAND["fonts"]["heading"] in charts.build_chart(
              "bar", BAR_DATA, title="t"))
    # Org override via the brand= kwarg (the caller resolves org_id).
    entities = {
        "workspace": {"brand": {"palette": {"accent": "8A5A2B"}}},
        "entities": {"orgs": [{"id": "org_x",
                               "brand": {"palette": {"accent": "112233"}}}]},
    }
    ws_brand = get_brand(entities)
    org_brand = get_brand(entities, "org_x")
    check("workspace override accent paints",
          "#8A5A2B" in charts.build_chart("bar", BAR_DATA, brand=ws_brand))
    svg_org = charts.build_chart("bar", BAR_DATA, brand=org_brand)
    check("org override beats workspace accent",
          "#112233" in svg_org and "#8A5A2B" not in svg_org)


def test_golden_determinism():
    """Same input + same brand → same bytes, pinned against the checked-in
    golden. A deliberate renderer change re-baselines the golden in the same
    commit — a FAIL here on an unrelated change is drift, chase it."""
    svg = charts.build_chart("line", LINE_DATA, title="Month over month")
    check("build_chart is call-stable",
          svg == charts.build_chart("line", LINE_DATA, title="Month over month"))
    golden = GOLDEN.read_text(encoding="utf-8") if GOLDEN.is_file() else None
    check("golden file exists (tests/golden/chart_line_golden.svg)",
          golden is not None)
    if golden is not None:
        check("line SVG matches the golden byte-for-byte", svg == golden)


def test_leak_scan_on_chart_text():
    leaky = {"series": [{"label": "project_007 hours", "points": [
        {"x": "May", "y": 1}, {"x": "Jun", "y": 2}]}]}
    try:
        charts.build_chart("line", leaky)
        check("leak in chart text raises ChartLeakError", False, "no raise")
    except charts.ChartLeakError:
        check("leak in chart text raises ChartLeakError", True)
    check("chokepoint collapses a leak to None (chart dropped, no crash)",
          charts.try_chart_png({"kind": "line", "data": leaky}) is None)
    try:
        charts.build_chart("bar", dict(BAR_DATA), title="Phase 3 walk")
        check("leak in title raises ChartLeakError", False, "no raise")
    except charts.ChartLeakError:
        check("leak in title raises ChartLeakError", True)


def test_nice_ticks_frame_never_overflows():
    """Regression pin for the mid-build _nice_ticks fix (a bar overflowed the
    frame through the title: data max 52K, top tick 40K). The invariant: the
    tick range BRACKETS the data — top tick clears the max, bottom tick clears
    the min — symmetrically for negative maxima and degenerate all-equal
    inputs. Mutation-verified against the pre-fix shape (a tick loop that
    stops short of the max) at review time."""
    cases = [
        ("the reported 52K case", 0.0, 52000.0),
        ("negative maxima (all-negative series)", -52000.0, -8000.0),
        ("mixed sign", -30000.0, 52000.0),
        ("all-equal positive", 5.0, 5.0),
        ("all-equal negative", -5.0, -5.0),
        ("all-equal zero", 0.0, 0.0),
        ("sub-unit span", 0.0, 0.7),
    ]
    for name, lo, hi in cases:
        ticks = charts._nice_ticks(lo, hi)
        # The axis always includes zero; the frame brackets the data.
        frame_lo, frame_hi = min(lo, 0.0), max(hi, 0.0)
        check(f"ticks bracket the data ({name})",
              ticks[0] <= frame_lo and ticks[-1] >= frame_hi - 1e-9,
              f"ticks {ticks} vs data [{lo}, {hi}]")
        check(f"ticks strictly increasing ({name})",
              all(b > a for a, b in zip(ticks, ticks[1:])))
        check(f"tick count sane ({name})", 2 <= len(ticks) <= 9,
              f"{len(ticks)} ticks")


def test_unit_is_scanned_chart_text():
    """'unit' renders into every axis tick and value label — it must join
    chart_strings (the deck plan scan input) and the pre-render leak scan
    (the T3.1 pixels-are-invisible class)."""
    leaky_unit = dict(BAR_DATA, unit="project_007")
    check("unit joins chart_strings",
          "project_007" in charts.chart_strings(
              {"kind": "bar", "data": leaky_unit}))
    try:
        charts.build_chart("bar", leaky_unit)
        check("leak in unit raises ChartLeakError", False, "no raise")
    except charts.ChartLeakError:
        check("leak in unit raises ChartLeakError", True)
    check("chokepoint collapses a unit leak to None",
          charts.try_chart_png({"kind": "bar", "data": leaky_unit}) is None)


def test_rasterizer_none_gracefully():
    svg = charts.build_chart("bar", BAR_DATA)
    check("empty ladder returns None (no rasterizer — CI shape)",
          _no_raster(lambda: charts.rasterize_svg(svg)) is None)
    prior = os.environ.get("CR_CHART_RASTER")
    try:
        os.environ["CR_CHART_RASTER"] = "off"
        check("CR_CHART_RASTER=off forces the skipped path",
              charts.rasterize_svg(svg) is None)
    finally:
        if prior is None:
            os.environ.pop("CR_CHART_RASTER", None)
        else:
            os.environ["CR_CHART_RASTER"] = prior
    check("garbage input returns None without raising",
          charts.rasterize_svg("not an svg") is None)


def test_chokepoint_success_path():
    """A fake rung proves try_chart_png returns the rung's PNG when a
    rasterizer exists — without depending on the host machine."""
    def fake_rung(svg_path, out_png, w, h):
        Path(out_png).write_bytes(b"\x89PNG fake")
        return out_png
    original = charts._SVG_TO_PNG_LADDER
    try:
        charts._SVG_TO_PNG_LADDER = (fake_rung,)
        png = charts.try_chart_png({"kind": "bar", "data": BAR_DATA})
    finally:
        charts._SVG_TO_PNG_LADDER = original
    check("try_chart_png returns the ladder's PNG path",
          bool(png) and Path(png).is_file())


def test_selection_caps_prose_pins():
    """CHART_SELECTION.md's caps table mirrors charts.SELECTION_CAPS (the
    DECK_GRAMMAR pattern) — change one, change the other, same commit."""
    prose = (ROOT / "shared" / "CHART_SELECTION.md").read_text(encoding="utf-8")
    caps = charts.SELECTION_CAPS
    check("selection prose pins max line series",
          f"| max line series | {caps['max_line_series']} |" in prose)
    check("selection prose pins min line points",
          f"| min points per line series | {caps['min_line_points']} |" in prose)
    check("selection prose pins max grouped series",
          f"| max grouped-bar series | {caps['max_grouped_series']} |" in prose)
    check("selection prose pins donut slice range",
          f"| donut slices | {caps['min_donut_slices']}–{caps['max_donut_slices']} |" in prose)
    check("selection prose pins waterfall step range",
          f"| waterfall steps | {caps['min_waterfall_steps']}–{caps['max_waterfall_steps']} |" in prose)
    notices = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    check("THIRD_PARTY_NOTICES carries the AntV MIT notice",
          "antvis/chart-visualization-skills" in notices
          and "MIT License" in notices)
    check("CHART_SELECTION points at the notice",
          "THIRD_PARTY_NOTICES.md" in prose)


def test_docx_byte_identical_without_rasterizer():
    """Acceptance #1: a machine with no rasterizer renders a charted payload
    BYTE-IDENTICAL to the same payload with no charts key."""
    from brief_writer import make_brief
    sections_plain = [{
        "heading": "KPIs vs targets",
        "table": {"rows": [["MRR", "$420K", "$450K"]],
                  "headers": ["Metric", "Current", "Target"]},
    }]
    sections_charted = [{
        "heading": "KPIs vs targets",
        "table": {"rows": [["MRR", "$420K", "$450K"]],
                  "headers": ["Metric", "Current", "Target"]},
        "charts": [{"kind": "bar_grouped", "data": GROUPED_DATA,
                    "title": "KPIs vs targets"}],
    }]
    prior = os.environ.get("CR_CHART_RASTER")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CR_CHART_RASTER"] = "off"
            a = os.path.join(tmp, "plain.docx")
            b = os.path.join(tmp, "charted.docx")
            make_brief(a, brief_kind="memo", title="t", subtitle="s",
                       exec_header={"verdict": "v."}, sections=sections_plain,
                       contract="off", voice_gate="off")
            make_brief(b, brief_kind="memo", title="t", subtitle="s",
                       exec_header={"verdict": "v."}, sections=sections_charted,
                       contract="off", voice_gate="off")
            check("docx: charted payload byte-identical with no rasterizer",
                  _zip_payload_identical(a, b))
        finally:
            if prior is None:
                os.environ.pop("CR_CHART_RASTER", None)
            else:
                os.environ["CR_CHART_RASTER"] = prior

    # A charts-only section is refused — the fallback is structural.
    with tempfile.TemporaryDirectory() as tmp:
        try:
            make_brief(os.path.join(tmp, "x.docx"), brief_kind="memo",
                       title="t", subtitle="s",
                       exec_header={"verdict": "v."},
                       sections=[{"heading": "H", "charts":
                                  [{"kind": "bar", "data": BAR_DATA}]}],
                       contract="off", voice_gate="off")
            check("docx: charts-only section refused", False, "no raise")
        except ValueError:
            check("docx: charts-only section refused", True)


def test_docx_renders_chart_with_fake_rasterizer():
    """With a working rasterizer the charted docx differs (the image landed)."""
    from brief_writer import make_brief
    # A real 1x1 PNG so python-docx can size it.
    png_1x1 = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
        "0000000d4944415478da63fcffff3f030005fe02fea72d994b0000000049454e44"
        "ae426082")

    def fake_rung(svg_path, out_png, w, h):
        Path(out_png).write_bytes(png_1x1)
        return out_png

    sections = [{
        "heading": "KPIs vs targets",
        "table": {"rows": [["MRR", "$420K"]]},
        "charts": [{"kind": "bar_grouped", "data": GROUPED_DATA}],
    }]
    original = charts._SVG_TO_PNG_LADDER
    with tempfile.TemporaryDirectory() as tmp:
        try:
            charts._SVG_TO_PNG_LADDER = (fake_rung,)
            a = os.path.join(tmp, "with_chart.docx")
            b = os.path.join(tmp, "without.docx")
            make_brief(a, brief_kind="memo", title="t", subtitle="s",
                       exec_header={"verdict": "v."}, sections=sections,
                       contract="off", voice_gate="off")
            make_brief(b, brief_kind="memo", title="t", subtitle="s",
                       exec_header={"verdict": "v."},
                       sections=[{k: v for k, v in sections[0].items()
                                  if k != "charts"}],
                       contract="off", voice_gate="off")
            check("docx: chart image lands when a rasterizer exists",
                  Path(a).read_bytes() != Path(b).read_bytes())
        finally:
            charts._SVG_TO_PNG_LADDER = original


def test_premium_html_inline_svg():
    """HTML backend: the SVG embeds inline in .chart-slot (no rasterizer
    involved); a refused chart leaves the section untouched; charts-only
    sections are refused."""
    from premium_html import make_premium_brief
    base = dict(brief_kind="memo", title="t", subtitle="s",
                exec_header={"verdict": "v."}, contract="off", voice_gate="off")
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "brief.html")
        make_premium_brief(p, sections=[{
            "heading": "Trend", "body": "Numbers below.",
            "charts": [{"kind": "line", "data": LINE_DATA,
                        "title": "Month over month"}],
        }], **base)
        html_text = Path(p).read_text(encoding="utf-8")
        check("premium html embeds the chart SVG inline",
              '<figure class="chart-slot"><svg' in html_text)
        p2 = os.path.join(tmp, "refused.html")
        make_premium_brief(p2, sections=[{
            "heading": "Trend", "body": "Numbers below.",
            "charts": [{"kind": "line", "data": {"series": [
                {"label": "t", "points": [{"x": "a", "y": 1}]}]}}],
        }], **base)
        check("premium html drops a refused chart (no empty frame)",
              '<figure class="chart-slot"' not in Path(p2).read_text(encoding="utf-8"))
        try:
            make_premium_brief(os.path.join(tmp, "x.html"), sections=[{
                "heading": "H",
                "charts": [{"kind": "line", "data": LINE_DATA}],
            }], **base)
            check("premium html: charts-only section refused", False, "no raise")
        except ValueError:
            check("premium html: charts-only section refused", True)


def test_deck_plan_and_paint_fallback():
    """The OUT6 seam: the KPI slide plan carries ONE valid chart spec (invalid
    specs dropped, never raised — the deck is not stricter than its pack);
    chart strings join the plan leak scan; with no rasterizer the painted deck
    is byte-identical to a chartless one (the table fallback)."""
    import deck_writer
    kpi_section = {
        "heading": "KPIs vs Targets",
        "tiles": [{"label": "MRR", "value": "$420K"}],
        "table": {"rows": [["MRR", "$420K", "$450K"]],
                  "headers": ["Metric", "Current", "Target"]},
        "charts": [
            {"kind": "donut", "data": {"slices": []}},  # invalid — dropped
            {"kind": "bar_grouped", "data": GROUPED_DATA, "title": "KPIs"},
            {"kind": "bar", "data": BAR_DATA},  # second valid — not carried
        ],
    }
    plan = deck_writer.build_slide_plan(
        [kpi_section], title="Verdict.", subtitle="Q3 — Sample Co")
    kpi_slides = [s for s in plan if s["slide"] == "kpi"]
    check("kpi slide in plan", len(kpi_slides) == 1)
    if kpi_slides:
        carried = kpi_slides[0].get("charts") or []
        check("plan carries exactly ONE valid chart spec",
              len(carried) == 1 and carried[0]["kind"] == "bar_grouped")
        check("table stays in the plan as the fallback",
              bool(kpi_slides[0].get("table")))
        strings = deck_writer._plan_strings(plan)
        check("chart strings join the plan leak-scan input",
              "Current" in strings and "MRR" in strings and "KPIs" in strings)

    prior = os.environ.get("CR_CHART_RASTER")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CR_CHART_RASTER"] = "off"
            a = os.path.join(tmp, "charted.pptx")
            b = os.path.join(tmp, "plain.pptx")
            deck_writer.make_deck(a, [kpi_section], title="Verdict.",
                                  subtitle="Q3 — Sample Co")
            deck_writer.make_deck(
                b, [{k: v for k, v in kpi_section.items() if k != "charts"}],
                title="Verdict.", subtitle="Q3 — Sample Co")
            check("pptx: charted payload byte-identical with no rasterizer",
                  _zip_payload_identical(a, b))
        except deck_writer.DeckDependencyError:
            print("SKIP pptx byte-identity — python-pptx unavailable and "
                  "uninstallable on this machine")
            check("pptx: charted payload byte-identical with no rasterizer",
                  False, "python-pptx unavailable — install it to run the battery")
        finally:
            if prior is None:
                os.environ.pop("CR_CHART_RASTER", None)
            else:
                os.environ["CR_CHART_RASTER"] = prior


def test_value_receipt_trend_helper():
    from value_receipt import build_trend_chart, compute_metrics
    per_month = [
        {"month": "2026-04", "label": "Apr 2026", "hours_estimate": 8.0},
        {"month": "2026-05", "label": "May 2026", "hours_estimate": 12.5},
        {"month": "2026-06", "label": "Jun 2026", "hours_estimate": 9.0},
    ]
    spec = build_trend_chart(per_month)
    check("trend helper returns a line spec", bool(spec) and spec["kind"] == "line")
    if spec:
        pts = spec["data"]["series"][0]["points"]
        check("trend points verbatim from per_month rows",
              [p["y"] for p in pts] == [8.0, 12.5, 9.0]
              and [p["x"] for p in pts] == ["Apr 2026", "May 2026", "Jun 2026"])
        # The spec must actually render (round-trip through build_chart).
        ET.fromstring(charts.build_chart(spec["kind"], spec["data"],
                                         title=spec.get("title")))
        check("trend spec renders", True)
    check("trend helper None below 2 months",
          build_trend_chart(per_month[:1]) is None)
    check("trend helper None on all-zero",
          build_trend_chart([
              {"label": "Apr", "hours_estimate": 0.0},
              {"label": "May", "hours_estimate": 0.0}]) is None)
    check("trend helper None on a missing metric (never chart a gap as zero)",
          build_trend_chart([{"label": "Apr", "hours_estimate": 1.0},
                             {"label": "May"}]) is None)

    # Quarterly sections carry the chart next to the table (same numbers).
    computed = compute_metrics(
        [{"type": "meeting_processed", "ts": "2026-04-10T10:00:00Z"},
         {"type": "meeting_processed", "ts": "2026-05-10T10:00:00Z"}],
        "2026-04-01T00:00:00Z", "2026-07-01T00:00:00Z")
    from value_receipt import _build_sections
    secs = _build_sections(computed["metrics"], computed["hours_estimate"],
                           computed["per_month"], "quarter")
    mbm = [s for s in secs if s["heading"] == "Month by month"]
    check("quarterly Month-by-month section exists", len(mbm) == 1)
    if mbm:
        chart_specs = mbm[0].get("charts") or []
        check("quarterly section carries the MoM trend chart",
              len(chart_specs) == 1 and chart_specs[0]["kind"] == "line")
        check("quarterly section keeps its table (the fallback)",
              bool(mbm[0].get("table")))


def test_pipeline_stage_mix():
    from pipeline_math import stage_mix
    rows = [
        {"deal": {"stage": "lead"}}, {"deal": {"stage": "lead"}},
        {"deal": {"stage": "proposal_sent"}},
        {"deal": {"stage": "negotiating"}},
        {},                       # untracked — contributes nothing
        {"deal": {"stage": "bogus"}},  # unknown stage — ignored
    ]
    mix = stage_mix(rows)
    check("stage_mix counts per stage in canonical order",
          mix == [{"label": "Lead", "value": 2},
                  {"label": "Proposal sent", "value": 1},
                  {"label": "Negotiating", "value": 1}])
    check("stage_mix drops zero stages (no Qualified slice)",
          all(s["label"] != "Qualified" for s in mix))
    check("stage_mix reader-facing labels (no internal tokens)",
          all("_" not in s["label"] for s in mix))
    check("stage_mix empty input -> []", stage_mix([]) == [])
    ET.fromstring(charts.build_chart("donut", {"slices": mix},
                                     title="Pipeline by stage"))
    check("stage_mix slices render as the donut", True)
    check("single-stage mix refuses at the chart (donut needs 2+ slices)",
          charts.try_chart_png({"kind": "donut", "data": {
              "slices": stage_mix([{"deal": {"stage": "lead"}}])}}) is None)


def main():
    test_svg_well_formed_every_kind()
    test_refusal_rules()
    test_refusal_is_a_valueerror()
    test_brand_resolution_and_org_override()
    test_golden_determinism()
    test_leak_scan_on_chart_text()
    test_nice_ticks_frame_never_overflows()
    test_unit_is_scanned_chart_text()
    test_rasterizer_none_gracefully()
    test_chokepoint_success_path()
    test_selection_caps_prose_pins()
    test_docx_byte_identical_without_rasterizer()
    test_docx_renders_chart_with_fake_rasterizer()
    test_premium_html_inline_svg()
    test_deck_plan_and_paint_fallback()
    test_value_receipt_trend_helper()
    test_pipeline_stage_mix()

    print()
    if _failures:
        print(f"FAIL — {len(_failures)} charts check(s) failed")
        return 1
    print("OK — charts suite green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
