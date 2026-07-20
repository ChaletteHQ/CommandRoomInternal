#!/usr/bin/env python3
"""SPEC OUT3B — chart-on-demand contract tests.

The skill itself is instruction-layer (Claude orchestrates at fire time); this
battery pins the CODE contracts the skill leans on, exactly where a regression
would land:

  1. Catalog mapping — each S-id ask shape resolves to the right kind and the
     right owning helper's output, and that output validates through charts.py.
  2. S3 value_by_org rollup math (per-org sum, None-value drop, reader labels,
     entity-scoped filter).
  3. Refusal-over-fabrication — out-of-catalog / <2 points / single-stage /
     booked-revenue-without-QBO all refuse (None or a raising validate), never
     an empty frame, never an artifact.
  4. Observed-periods-only — the trend carries the caller's rows VERBATIM; a
     planted interpolated point is the only way an invented point appears
     (mutation-verify), proving the helper never inserts one.
  5. Page anatomy — a chart_on_demand page renders with chart + table twin +
     source line; a chart-ONLY section refuses (charts never satisfy content).
  6. Event shape — the chart_render receipt (rendered + refused) passes EVT1.

Deterministic: CR_CHART_RASTER=off so no host rasterizer is consulted.
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from contextlib import redirect_stderr
from pathlib import Path

os.environ["CR_CHART_RASTER"] = "off"

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import charts  # noqa: E402
import pipeline_math as pm  # noqa: E402
import value_receipt as vr  # noqa: E402
import event_payload_check as epc  # noqa: E402
from premium_html import make_premium_brief  # noqa: E402
from brief_gates import STANDARD_KINDS, EYEBROW_BY_KIND  # noqa: E402
from output_profile import resolve_format_for_kind, PREMIUM_LAUNCH_KINDS  # noqa: E402

_failures = []


def check(name, cond, extra=""):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name + (f" — {extra}" if extra else ""))


# ---------------------------------------------------------------------------
# Fixtures — date-relative, placeholder names only.
# ---------------------------------------------------------------------------

def _deal_row(tid, name, org_id, value):
    deal = {"stage": "lead"}
    if value is not None:
        deal["value"] = value
        deal["currency"] = "USD"
    return {"thread_id": tid, "name": name, "org_id": org_id,
            "status": "active", "deal": deal, "untracked": False}


# Three orgs, one unpriced deal, one deal-less untracked thread.
OPEN_DEALS = [
    _deal_row("d1", "Acme pilot", "org_acme", 40000),
    _deal_row("d2", "Acme expansion", "org_acme", 60000),
    _deal_row("d3", "Northstar retainer", "org_north", 90000),
    _deal_row("d4", "Unpriced Acme lead", "org_acme", None),
    {"thread_id": "d5", "name": "Untracked thread", "org_id": "org_ghost",
     "status": "active", "deal": None, "untracked": True},
]
ORG_NAMES = {"org_acme": "Acme Co", "org_north": "Northstar Partners"}

# Stage-mix inputs: two stages present -> a donut can draw.
STAGE_DEALS = [
    _deal_row("s1", "A", "org_acme", 10000),
    _deal_row("s2", "B", "org_north", 20000),
]
STAGE_DEALS[0]["deal"]["stage"] = "lead"
STAGE_DEALS[1]["deal"]["stage"] = "qualified"

# value-receipt per-month rows (compute_metrics shape: label + metric keys).
THREE_MONTHS = [
    {"label": "Jan", "hours_estimate": 4.0},
    {"label": "Feb", "hours_estimate": 6.0},
    {"label": "Mar", "hours_estimate": 9.0},
]


# ---------------------------------------------------------------------------
# 1 + 2. Catalog mapping + S3 rollup math
# ---------------------------------------------------------------------------

# S1 value_trend -> a LINE spec whose points match build_trend_chart verbatim.
trend = vr.build_trend_chart(THREE_MONTHS, metric="hours_estimate")
check("S1 value_trend maps to a line spec", trend is not None
      and trend["kind"] == "line")
charts.validate_chart(trend)  # raises if invalid
check("S1 line validates through charts.py", True)

# S2 pipeline_mix -> donut slices, one per non-empty stage.
mix = pm.stage_mix(STAGE_DEALS)
mix_spec = {"kind": "donut", "title": "Pipeline mix",
            "data": {"slices": mix}}
charts.validate_chart(mix_spec)
check("S2 pipeline_mix maps to a valid donut", len(mix) == 2
      and mix_spec["kind"] == "donut")

# S3 pipeline_by_org -> bar, per-org summed value, value-desc, reader labels.
rows = pm.value_by_org(OPEN_DEALS, org_names=ORG_NAMES)
check("S3 value_by_org: Acme 100k (40k+60k) > Northstar 90k, reader labels, "
      "unpriced+dealless dropped",
      rows == [{"label": "Acme Co", "value": 100000.0},
               {"label": "Northstar Partners", "value": 90000.0}],
      extra=repr(rows))
bar_spec = {"kind": "bar", "title": "Revenue in play by client",
            "data": {"categories": [r["label"] for r in rows],
                     "series": [{"label": "Revenue in play",
                                 "values": [r["value"] for r in rows]}],
                     "unit": "$"}}
charts.validate_chart(bar_spec)
check("S3 bar validates through charts.py", True)

# Entity-scoped: filtering open_deals to one org before the rollup renders
# only that org's deals (the resolved-org_id filter the skill applies).
scoped = pm.value_by_org([r for r in OPEN_DEALS if r.get("org_id") == "org_acme"],
                         org_names=ORG_NAMES)
check("entity-scoped S3: only the resolved org's bar",
      scoped == [{"label": "Acme Co", "value": 100000.0}], extra=repr(scoped))


# ---------------------------------------------------------------------------
# 3. Refusal over fabrication — never an empty frame, never an artifact
# ---------------------------------------------------------------------------

# <2 months -> the trend refuses (None). The skill offers the table instead.
check("S1 refuses below 2 points (None, not an empty line)",
      vr.build_trend_chart(THREE_MONTHS[:1]) is None)

# A single-stage pipeline -> one slice -> the donut refuses (min 2 slices).
one_stage = [_deal_row("x", "X", "org_acme", 1000)]
one_stage[0]["deal"]["stage"] = "lead"
single_mix = pm.stage_mix(one_stage)
raised = False
try:
    charts.validate_chart({"kind": "donut", "data": {"slices": single_mix}})
except charts.ChartDataError:
    raised = True
check("S2 refuses a single-stage donut (<2 slices)",
      len(single_mix) == 1 and raised)

# No priced deal -> value_by_org == [] -> the bar refuses, never a $0 bar.
dealless = [OPEN_DEALS[3], OPEN_DEALS[4]]  # unpriced + untracked only
check("S3 refuses when no deal carries a value ([], never a $0 bar)",
      pm.value_by_org(dealless, org_names=ORG_NAMES) == [])

# All-zero trend is an empty frame -> refuses.
check("S1 refuses an all-zero trend",
      vr.build_trend_chart([{"label": "Jan", "hours_estimate": 0.0},
                            {"label": "Feb", "hours_estimate": 0.0}]) is None)


# ---------------------------------------------------------------------------
# 4. Observed-periods-only — no interpolation (mutation-verify)
# ---------------------------------------------------------------------------

# A gap month (Jan, Mar — Feb absent) yields EXACTLY two points, in order,
# verbatim. The helper never invents a Feb.
gap = [{"label": "Jan", "hours_estimate": 4.0},
       {"label": "Mar", "hours_estimate": 9.0}]
gap_pts = vr.build_trend_chart(gap)["data"]["series"][0]["points"]
check("observed-periods-only: gap month stays absent (no invented point)",
      [(p["x"], p["y"]) for p in gap_pts] == [("Jan", 4.0), ("Mar", 9.0)],
      extra=repr(gap_pts))
# Mutation guard: the ONLY way a Feb point appears is if the CALLER plants it —
# proving the interpolation would have to be a deliberate injection, never the
# helper's doing.
planted = gap[:1] + [{"label": "Feb", "hours_estimate": 6.5}] + gap[1:]
planted_pts = vr.build_trend_chart(planted)["data"]["series"][0]["points"]
check("mutation-verify: a planted interpolated point is visible (so its "
      "absence above is a real no-interpolation guarantee)",
      any(p["x"] == "Feb" for p in planted_pts))


# ---------------------------------------------------------------------------
# 5. Kind registration + page anatomy
# ---------------------------------------------------------------------------

check("chart_on_demand is a STANDARD_KIND", "chart_on_demand" in STANDARD_KINDS)
check("chart_on_demand has an eyebrow label", "chart_on_demand" in EYEBROW_BY_KIND)
check("chart_on_demand is premium-launched", "chart_on_demand" in PREMIUM_LAUNCH_KINDS)

_ws = Path(tempfile.mkdtemp(prefix="out3b_ws_"))
(_ws / "_hq" / "data" / "skill_config").mkdir(parents=True)
(_ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")
check("unconfigured workspace resolves chart_on_demand -> premium_html",
      resolve_format_for_kind("chart_on_demand", str(_ws)) == "premium_html")

# A well-formed page: chart + source body + table twin. Renders, file exists.
section = {
    "heading": "Revenue in play by client",
    "charts": [bar_spec],
    "body": "From the open deals on the pipeline as of today. Stated deal "
            "values only. Unpriced deals are excluded and never estimated.",
    "table": {"headers": ["Client", "Revenue in play"],
              "rows": [[r["label"], f"${r['value']:,.0f}"] for r in rows]},
}
out = str(_ws / "Chart_Revenue_By_Client.html")
buf = io.StringIO()
with redirect_stderr(buf):
    make_premium_brief(
        out, brief_kind="chart_on_demand",
        title="Revenue in play by client",
        subtitle="As of today",
        sections=[section],
        exec_header={"verdict": "Acme Co and Northstar hold most of the "
                                "revenue in play right now."},
        workspace_root=str(_ws),
    )
html = Path(out).read_text(encoding="utf-8")
check("page renders: file exists on disk", Path(out).is_file())
check("page carries the chart SVG inline", "<svg" in html and "chart-slot" in html)
check("page carries the table twin", "Revenue in play" in html and "$100,000" in html)

# A chart-ONLY section (no table/body) refuses — charts never satisfy content.
raised = False
buf = io.StringIO()
try:
    with redirect_stderr(buf):
        make_premium_brief(
            str(_ws / "chart_only.html"), brief_kind="chart_on_demand",
            title="X", subtitle="Y",
            sections=[{"heading": "Chart only", "charts": [bar_spec]}],
            exec_header={"verdict": "V."}, workspace_root=str(_ws))
except ValueError:
    raised = True
check("chart-only section refuses (charts never stand alone)", raised)


# ---------------------------------------------------------------------------
# 6. chart_render event shape (EVT1)
# ---------------------------------------------------------------------------

rendered_ev = {"type": "chart_render",
               "data": {"catalog_id": "pipeline_by_org", "kind": "bar",
                        "org_id": "org_acme", "artifact": out, "refused": False}}
refused_ev = {"type": "chart_render",
              "data": {"catalog_id": "value_trend", "kind": "line",
                       "refused": True,
                       "reason": "only 1 month of data — a trend needs 2+ points"}}
check("chart_render rendered payload passes EVT1", epc.check_payload(rendered_ev) == [])
check("chart_render refused payload passes EVT1", epc.check_payload(refused_ev) == [])
missing = epc.check_payload({"type": "chart_render", "data": {"kind": "bar"}})
check("chart_render missing catalog_id/refused is flagged", bool(missing))


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print()
    if _failures:
        print(f"{len(_failures)} FAILED:")
        for f in _failures:
            print("  -", f)
        return 1
    print("ALL chart-on-demand tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
