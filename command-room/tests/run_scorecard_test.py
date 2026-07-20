#!/usr/bin/env python3
"""Test battery for shared/scripts/scorecard.py (SPEC OUT7 — KPI scorecard /
QBR pre-read).

Covers the spec's §5 plan:
  * KPI factor-out parity — build_kpi_section output byte-identical to the
    pinned golden (the "board-pack §2 renders byte-identical KPIs before/after
    the factor-out" guarantee; §6.2). board-pack §2 is this assembler's first
    caller, so the golden IS the pack's §2 contract.
  * streak / direction / watch derivations on real-data-shape readings, incl.
    the negative-burn / lower-is-better cases and the green->warn crossing that
    the watch flag exists to catch.
  * data-through date always present (and REQUIRED — empty refuses).
  * exec-header rule for the new kind (verdict = the most decision-relevant KPI
    move) + the brief-family CHANGED/DECIDE/NEEDED lines.
  * kind wiring: kpi_scorecard in EYEBROW_BY_KIND / STANDARD_KINDS /
    RULES_BY_KIND, NOT eyebrow-excluded; renders through both backends with the
    exec header enforced; the KPI tables carry no blank cells.
  * no EOS vocabulary in any rendered string (core-surface fence).
  * charts go through charts.py only (spec shapes; no second SVG path).
  * the OPT-IN monthly-scorecard scheduled job: registered in OPTIONAL_JOBS +
    the receipt vocabularies, absent confirmation => not due, enabled => due.

FIXTURE DATES: `data_through` is computed relative to today (a past date) so it
can never become a G14 time bomb — scorecard.py reads no clock and never
compares it to one, but the guard scans literals, so we keep it relative.

House convention: check(name, cond), non-zero exit, stdlib only. Charts are
forced to the skipped path (CR_CHART_RASTER=off) so a machine's rasterizer
never makes the render assertions flaky.
"""
from __future__ import annotations

import datetime as _dt
import os
import sys
import tempfile
from pathlib import Path

os.environ["CR_CHART_RASTER"] = "off"  # charts drop to spec-only; no rasterize

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import scorecard as sc  # noqa: E402

FAILURES = []


def check(name, cond, detail=""):
    if cond:
        print(f"  PASS  {name}")
    else:
        FAILURES.append(name)
        print(f"  FAIL  {name}  {detail}")


# A past date (stays past forever) — scorecard reads no clock, this is a label.
DATA_THROUGH = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()


# Real-data-shape readings: dollar / percent / count / negative-burn (higher-is-
# better) / months, plus one drop-empty KPI (no current value).
READINGS = [
    {"name": "MRR", "unit": "$", "current": 478000, "target": 470000,
     "history": [{"current": 420000, "target": 450000},
                 {"current": 455000, "target": 460000}]},
    {"name": "NRR", "unit": "%", "current": 118, "target": 125,
     "history": [{"current": 130, "target": 125},
                 {"current": 128, "target": 125}]},
    {"name": "New customers", "unit": "", "current": 8, "target": 6,
     "history": [{"current": 5, "target": 6}]},
    {"name": "Burn", "unit": "$", "current": -31000, "target": -40000,
     "higher_is_better": True,
     "history": [{"current": -45000, "target": -40000}]},
    {"name": "Runway", "unit": "mo", "current": 18.4, "target": 18,
     "history": [{"current": 17, "target": 18}]},
    {"name": "Pipeline coverage", "unit": "", "current": None, "target": 3,
     "history": []},
]


# The pinned golden for build_kpi_section(READINGS) — byte-identical parity
# (SPEC OUT7 §6.2). If this dict changes, board-pack §2 rendered differently:
# re-confirm the change is intended before updating the golden.
KPI_SECTION_GOLDEN = {
    "heading": "KPIs vs Targets",
    "table": {
        "headers": ["Metric", "Current", "Target", "Vs target", "Trend"],
        "rows": [
            ["MRR", "$478K", "$470K", "+1.7%", "▲"],
            ["NRR", "118%", "125%", "-7 pts", "▼"],
            ["New customers", "8", "6", "+33.3%", "▲"],
            ["Burn", "-$31K", "-$40K", "+22.5%", "▲"],
            ["Runway", "18.4 mo", "18 mo", "+2.2%", "▲"],
            ["Pipeline coverage", "—", "3", "—", "▬"],
        ],
    },
    "tiles": [
        {"label": "MRR ▲", "value": "$478K"},
        {"label": "NRR ▼", "value": "118%"},
        {"label": "New customers ▲", "value": "8"},
        {"label": "Burn ▲", "value": "-$31K"},
        {"label": "Runway ▲", "value": "18.4 mo"},
    ],
    "charts": [
        {
            "kind": "bar_grouped",
            "title": "KPIs vs targets",
            "data": {
                "categories": ["MRR", "Burn"],
                "series": [
                    {"label": "Current", "values": [478000.0, -31000.0]},
                    {"label": "Target", "values": [470000.0, -40000.0]},
                ],
                "unit": "$",
            },
        }
    ],
}


def _all_text(sections):
    """Every user-facing string in a sections payload — for the EOS-vocab scan."""
    out = []
    for s in sections:
        out.append(s.get("heading", ""))
        out.append(s.get("body", "") or "")
        for b in s.get("bullets", []) or []:
            out.append(b)
        tbl = s.get("table") or {}
        for row in tbl.get("rows", []) or []:
            out.extend(str(c) for c in row)
        out.extend(tbl.get("headers", []) or [])
    return " ".join(out)


def main():
    print("== format_value determinism")
    check("dollar K scaling", sc.format_value(478000, "$") == "$478K")
    check("dollar M scaling", sc.format_value(2500000, "$") == "$2.5M")
    check("negative dollar", sc.format_value(-31000, "$") == "-$31K")
    check("percent tight suffix", sc.format_value(118, "%") == "118%")
    check("months spaced suffix", sc.format_value(18.4, "mo") == "18.4 mo")
    check("bare count", sc.format_value(8, "") == "8")
    check("zero is a real value", sc.format_value(0, "$") == "$0")
    check("None -> empty", sc.format_value(None, "$") == "")
    check("K scaling starts at 1e3 (pins the threshold — mutation cover)",
          sc.format_value(5000, "$") == "$5K" and sc.format_value(999, "$") == "$999")

    print("== compute_kpi_rows derivations")
    rows = sc.compute_kpi_rows(READINGS)
    by = {r["name"]: r for r in rows}
    check("MRR on target, up vs prior",
          by["MRR"]["status"] == "ok" and by["MRR"]["direction"] == sc.ARROW_UP
          and by["MRR"]["vs_target"] == "+1.7%")
    check("NRR off target (bad), down vs prior, points delta",
          by["NRR"]["status"] == "bad" and by["NRR"]["direction"] == sc.ARROW_DOWN
          and by["NRR"]["vs_target"] == "-7 pts")
    check("negative-burn higher-is-better reads as ON target",
          by["Burn"]["status"] == "ok" and by["Burn"]["direction"] == sc.ARROW_UP)
    check("drop-empty KPI: no status to grade, em-dash-able",
          by["Pipeline coverage"]["status"] == "none"
          and by["Pipeline coverage"]["current_disp"] == "")

    print("== streak (consecutive periods on the same side of target)")
    # MRR: prior periods 420k(<450k target)=below, 455k(<460k)=below, now above.
    # So the current 'above' streak is just this period => 1.
    check("MRR above-target streak resets to 1 (priors were below)",
          by["MRR"]["streak"] == {"count": 1, "above": True, "label": "1 on"})
    # A 3-period unbroken above-target streak.
    streaky = sc.compute_kpi_rows([{
        "name": "Gross margin", "unit": "%", "current": 82, "target": 80,
        "history": [{"current": 81, "target": 80}, {"current": 83, "target": 80}],
    }])[0]
    check("unbroken above streak counts all periods",
          streaky["streak"]["count"] == 3 and streaky["streak"]["above"] is True)

    print("== watch flag = green->warn/bad crossing ONLY")
    # NRR: prior 128 (>=125 ok) -> now 118 (bad). A real crossing.
    check("NRR watch fires (was on target last period, off now)",
          by["NRR"]["watch"]["flag"] is True and "crossed" in by["NRR"]["watch"]["reason"])
    # A KPI off target for TWO periods running does NOT re-fire watch.
    chronic = sc.compute_kpi_rows([{
        "name": "Churn", "unit": "%", "current": 4, "target": 2,
        "higher_is_better": False,
        "history": [{"current": 3, "target": 2}],  # already off last period
    }])[0]
    check("chronic-miss KPI does NOT fire watch (no fresh crossing)",
          chronic["watch"]["flag"] is False and chronic["status"] == "bad")
    # No prior at all -> no crossing -> no watch even when off target.
    fresh_off = sc.compute_kpi_rows([{
        "name": "New KPI", "unit": "%", "current": 90, "target": 100, "history": [],
    }])[0]
    check("off target with no prior does not fire watch",
          fresh_off["watch"]["flag"] is False)

    print("== lower-is-better prose is direction-aware (second-eyes review fix)")
    # A churn-class KPI misses by rising ABOVE target — no output string may
    # say "below target" about it (the review's burn-class probe).
    churn_cross = sc.compute_kpi_rows([{
        "name": "Churn", "unit": "%", "current": 4, "target": 2,
        "higher_is_better": False,
        "history": [{"current": 1.8, "target": 2}],  # was on target -> crossed
    }])[0]
    check("hib=False watch reason says ABOVE target, never below",
          churn_cross["watch"]["flag"] is True
          and "above target" in churn_cross["watch"]["reason"]
          and "below" not in churn_cross["watch"]["reason"])
    churn_card = sc.build_scorecard(
        [{"name": "Churn", "unit": "%", "current": 4, "target": 2,
          "higher_is_better": False}],
        period="month", data_through=DATA_THROUGH)
    check("hib=False verdict says ABOVE target for the churn-class miss",
          "above target" in churn_card["exec_header"]["verdict"]
          and "below target" not in churn_card["exec_header"]["verdict"])

    print("== tile band: drop-empty, cap, target-delta arrows")
    # Tile arrow is NUMERIC target-delta (matches the Δ cell's sign), never
    # goodness: churn under target (good) still points ▼ (numerically below).
    churn_good = sc.compute_kpi_rows([{
        "name": "Churn", "unit": "%", "current": 1.5, "target": 2,
        "higher_is_better": False}])
    check("hib=False tile arrow stays numeric (below target -> ▼ even when good)",
          sc.build_tile_band(churn_good) == [{"label": "Churn ▼", "value": "1.5%"}])
    tiles = sc.build_tile_band(rows)
    check("empty-current KPI gets NO tile (drop-empty)",
          all("Pipeline coverage" not in t["label"] for t in tiles))
    check("tile band capped at 5", len(tiles) <= sc.TILE_BAND_CAP)
    check("on-target tile carries ▲, off-target ▼",
          {"label": "MRR ▲", "value": "$478K"} in tiles
          and {"label": "NRR ▼", "value": "118%"} in tiles)

    print("== build_kpi_section GOLDEN (board-pack §2 factor-out parity)")
    section = sc.build_kpi_section(READINGS, include_chart=True)
    check("build_kpi_section byte-identical to the pinned golden",
          section == KPI_SECTION_GOLDEN,
          detail="section drifted from golden — a board-pack §2 change")
    check("no blank cells in the KPI table (em dash, never empty)",
          all(all(str(c).strip() != "" for c in row)
              for row in section["table"]["rows"]))

    print("== charts: one chart owner (charts.py), spec-only")
    bar = sc.kpi_dollar_bar_spec(rows)
    check("dollar bar is a bar_grouped Current-vs-Target spec",
          bar["kind"] == "bar_grouped"
          and [s["label"] for s in bar["data"]["series"]] == ["Current", "Target"]
          and bar["data"]["unit"] == "$")
    check("fewer than 2 dollar KPIs -> no bar",
          sc.kpi_dollar_bar_spec(sc.compute_kpi_rows(
              [{"name": "MRR", "unit": "$", "current": 5, "target": 4}])) is None)
    trend = sc.kpi_trend_line_spec(dict(by["NRR"], history=READINGS[1]["history"]))
    check("flagged-KPI trend is a line spec with >=2 points",
          trend["kind"] == "line" and len(trend["data"]["series"][0]["points"]) >= 2)
    check("single-point history -> no trend line",
          sc.kpi_trend_line_spec({"name": "X", "unit": "%", "current": 5,
                                  "target": 4, "history": []}) is None)
    import charts  # the specs must actually validate through the one owner
    charts.validate_chart(bar)
    charts.validate_chart(trend)
    check("scorecard chart specs validate through charts.validate_chart", True)

    print("== build_scorecard: data-through required + present in subtitle")
    try:
        sc.build_scorecard(READINGS, period="month", data_through="")
        check("empty data_through refuses", False)
    except sc.ScorecardInputError:
        check("empty data_through refuses", True)
    try:
        sc.build_scorecard(READINGS, period="year", data_through=DATA_THROUGH)
        check("unknown period refuses", False)
    except sc.ScorecardInputError:
        check("unknown period refuses", True)
    card = sc.build_scorecard(READINGS, period="month",
                              data_through=DATA_THROUGH, org_name="Acme Co")
    check("subtitle states the data-through date",
          f"data through {DATA_THROUGH}" in card["subtitle"])
    check("brief_kind is kpi_scorecard", card["brief_kind"] == sc.SCORECARD_KIND)

    print("== exec header: verdict = most decision-relevant move + brief-family lines")
    eh = card["exec_header"]
    check("verdict names the fresh watch crossing (NRR)",
          eh["verdict"].startswith("NRR") and "crossed" in eh["verdict"])
    check("CHANGED / DECIDE / NEEDED all present (brief-family)",
          all(eh.get(k, "").strip() for k in ("changed", "decide", "needs")))

    print("== month vs quarter sections (one generator, two periods)")
    m_headings = [s["heading"] for s in card["sections"]]
    check("month layout: KPIs vs Targets -> Scorecard -> ... -> Needs attention",
          m_headings[0] == "KPIs vs Targets" and m_headings[1] == "Scorecard"
          and m_headings[-1] == "Needs attention"
          and "Decisions logged this quarter" not in m_headings)
    check("needs-attention capped at 3",
          all(len(s.get("bullets", [])) <= 3 for s in card["sections"]
              if s["heading"] == "Needs attention"))
    qbr = sc.build_scorecard(READINGS, period="quarter", data_through=DATA_THROUGH,
                             decisions=["Paused the consumer pilot",
                                        "Locked pricing tiers"],
                             prior_period_note="MRR +18% vs last quarter")
    q_headings = [s["heading"] for s in qbr["sections"]]
    check("quarter adds a Decisions-logged section",
          "Decisions logged this quarter" in q_headings)
    check("QBR title distinct from the monthly scorecard",
          qbr["title"] == "QBR Pre-Read" and card["title"] == "KPI Scorecard")
    check("prior-quarter note rendered under the KPI section",
          any("MRR +18% vs last quarter" in (s.get("body") or "")
              for s in qbr["sections"]))
    month_noted = sc.build_scorecard(READINGS, period="month",
                                     data_through=DATA_THROUGH,
                                     prior_period_note="MRR +18% vs last quarter")
    check("prior_period_note is quarter-only: month card ignores it (review fix)",
          not any("last quarter" in (s.get("body") or "")
                  for s in month_noted["sections"]))

    print("== core-surface fence: no EOS vocabulary anywhere in the output")
    blob = (_all_text(card["sections"]) + " " + _all_text(qbr["sections"]) + " "
            + " ".join(card["exec_header"].values()) + " " + card["subtitle"]).lower()
    for banned in ("rock", "l10", "measurable"):
        check(f"no EOS term '{banned}' in output", banned not in blob)

    print("== kind wiring through the gate stack")
    from brief_gates import (EYEBROW_BY_KIND, STANDARD_KINDS,
                             EXEC_EYEBROW_EXCLUDED_KINDS)
    from output_contract_validator import RULES_BY_KIND
    check("kpi_scorecard has an eyebrow label",
          EYEBROW_BY_KIND.get("kpi_scorecard") == "KPI SCORECARD")
    check("kpi_scorecard is a STANDARD_KIND (exec header required)",
          "kpi_scorecard" in STANDARD_KINDS)
    check("kpi_scorecard is brief-family (NOT eyebrow-excluded)",
          "kpi_scorecard" not in EXEC_EYEBROW_EXCLUDED_KINDS)
    check("kpi_scorecard has contract rules (no blank cells)",
          RULES_BY_KIND.get("kpi_scorecard", {}).get("table_no_blank_cells") is True)

    print("== end-to-end render through both backends (exec header enforced)")
    from brief_writer import make_brief
    from premium_html import make_premium_brief
    with tempfile.TemporaryDirectory() as td:
        dp = str(Path(td) / "scorecard.docx")
        out = make_brief(dp, brief_kind=card["brief_kind"], title=card["title"],
                         subtitle=card["subtitle"], sections=card["sections"],
                         exec_header=card["exec_header"])
        check("docx render lands a file", Path(out).exists()
              and Path(out).stat().st_size > 0)
        hp = str(Path(td) / "scorecard.html")
        outh = make_premium_brief(hp, brief_kind=card["brief_kind"],
                                  title=card["title"], subtitle=card["subtitle"],
                                  sections=card["sections"],
                                  exec_header=card["exec_header"])
        html = Path(outh).read_text(encoding="utf-8")
        check("premium HTML carries the eyebrow + verdict + CHANGED line",
              "KPI SCORECARD" in html and "CHANGED" in html
              and card["exec_header"]["verdict"][:24] in html)
        # STANDARD_KIND without an exec header must be refused (the flip).
        try:
            make_brief(str(Path(td) / "no_header.docx"),
                       brief_kind="kpi_scorecard", title="X", subtitle="Y",
                       sections=[{"heading": "KPIs vs Targets",
                                  "bullets": ["a"]}])
            check("missing exec header refused for the new STANDARD_KIND", False)
        except ValueError:
            check("missing exec header refused for the new STANDARD_KIND", True)

    print("== opt-in scheduled job (never auto-registered)")
    import maintenance_dispatcher as md
    from receipts import RECEIPT_TYPES, CANONICAL_TASK_IDS
    check("monthly-scorecard registered in OPTIONAL_JOBS, not MAINTENANCE_JOBS",
          "monthly-scorecard" in md.OPTIONAL_JOBS
          and "monthly-scorecard" not in md.MAINTENANCE_JOBS)
    check("optional job carries skill/nominal_cron/description + opt_in flag",
          all(k in md.OPTIONAL_JOBS["monthly-scorecard"]
              for k in ("skill", "nominal_cron", "description", "opt_in")))
    check("core silent-job order pin is untouched by the optional job",
          list(md.MAINTENANCE_JOBS) == [
              "reconcile-sent", "session-sweep", "cleanup",
              "weekly-insights", "deal-signals", "identity-reconcile",
              "monthly-report"])
    check("receipt vocabulary registered (pack_run) + canonical id",
          RECEIPT_TYPES.get("monthly-scorecard", {}).get("types")
          == frozenset({"pack_run"})
          and "monthly-scorecard" in CANONICAL_TASK_IDS)

    now = _dt.datetime(2026, 8, 1, 6, 46)  # DATE_GUARD_OK: injected fire clock, never the real clock
    with tempfile.TemporaryDirectory() as td:
        import json
        ws = Path(td) / "ws"
        (ws / "_hq" / "data").mkdir(parents=True)
        (ws / "_hq" / "data" / "events.jsonl").write_text("", encoding="utf-8")

        def _write_entities(enabled):
            cfg = {"workspace": {}}
            if enabled is not None:
                cfg["workspace"]["schedule_config"] = {
                    "maintenance_jobs": {"monthly-scorecard": {"enabled": enabled}}}
            (ws / "_hq" / "data" / "entities.json").write_text(
                json.dumps(cfg), encoding="utf-8")

        _write_entities(None)
        due = [j["job_id"] for j in md.due_jobs(ws, now=now)]
        check("absent confirmation -> scorecard NOT due (never auto)",
              "monthly-scorecard" not in due)
        _write_entities(False)
        check("explicit disable -> not due",
              "monthly-scorecard" not in
              [j["job_id"] for j in md.due_jobs(ws, now=now)])
        _write_entities(True)
        check("opted in -> due at the first fire of the month",
              "monthly-scorecard" in
              [j["job_id"] for j in md.due_jobs(ws, now=now)])

        # Malformed opt-in values must stay inert (never-auto is load-bearing).
        for bad in ("true", 1, {"enabled": "true"}, {"enabled": 1}, {"on": True}):
            cfg = {"workspace": {"schedule_config": {
                "maintenance_jobs": {"monthly-scorecard": bad}}}}
            (ws / "_hq" / "data" / "entities.json").write_text(
                json.dumps(cfg), encoding="utf-8")
            if "monthly-scorecard" in [j["job_id"] for j in md.due_jobs(ws, now=now)]:
                check(f"malformed opt-in value {bad!r} stays inert", False)
                break
        else:
            check("malformed opt-in values (non-dict / wrong-typed) stay inert", True)

        # Receipt self-limit: once the job fires and writes its pack_run
        # receipt, it is NOT due again until the next month's slot (controlled
        # ts — the receipt is written for real, then pinned to the fire time).
        _write_entities(True)
        from receipts import log_receipt
        log_receipt(ws, "monthly-scorecard", receipt_type="pack_run",
                    fired_via="scheduled")
        evp = ws / "_hq" / "data" / "events.jsonl"
        lines = evp.read_text(encoding="utf-8").strip().splitlines()
        rec = json.loads(lines[-1])
        # Pin to the injected fire day, late enough UTC that any machine TZ
        # still lands it on/after the 1st-of-month slot in local time.
        rec["ts"] = now.strftime("%Y-%m-%dT23:46:00Z")
        lines[-1] = json.dumps(rec)
        evp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        mid_month = now + _dt.timedelta(days=15)
        check("receipted -> NOT due again mid-month (self-limits to monthly)",
              "monthly-scorecard" not in
              [j["job_id"] for j in md.due_jobs(ws, now=mid_month)])
        next_month = now + _dt.timedelta(days=36)  # past the next 1st-of-month slot
        check("next month's slot -> due again",
              "monthly-scorecard" in
              [j["job_id"] for j in md.due_jobs(ws, now=next_month)])

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("scorecard battery: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
