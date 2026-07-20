#!/usr/bin/env python3
"""KPI scorecard engine — the between-board-packs "how are we tracking" artifact
and its quarterly big sibling, the QBR pre-read (SPEC OUT7).

WHY THIS EXISTS
---------------
board-pack §2 (KPIs vs Targets) is the only place the workspace surfaces
KPIs-against-targets, and you cannot get that view without assembling a full
6–8-page board pack. operator-report covers value-of-the-system metrics;
weekly-recap is an events digest. The between-packs KPI view is genuinely
uncovered — this is the WorkBoard scorecard / MBR-QBR pre-read catalog item,
and Command Room's version wins on the same thesis it always does: every number
substrate-derived, computed in code, rendered verbatim, never re-typed.

ONE ENGINE, TWO CONSUMERS (the components.py move applied again)
----------------------------------------------------------------
`compute_kpi_rows` is the ONE implementation of the KPI-vs-target computation
(delta, direction, streak, watch). It has two consumers:

  1. `build_kpi_section` — the KPI SECTION assembler (tile band + table +
     the dollar Current-vs-Target bar). **board-pack §2 is its first caller**
     (its SKILL.md builds §2 by calling this, so the pack and the standalone
     scorecard render KPIs from one implementation — no drift).
  2. `build_scorecard` — the full standalone scorecard (tile band → scorecard
     table with streak + watch columns → per-flagged-KPI trend line → a
     ≤3-item "needs attention" block). `period="quarter"` makes it the QBR
     pre-read: a decisions-logged section + a prior-quarter comparison, same
     generator, NOT a second skill.

SUBSTRATE-DERIVED, NEVER RE-TYPED — WHERE THE NUMBERS COME FROM
--------------------------------------------------------------
There is no structured "KPI value" event type in the substrate: a board pack's
KPI current values are composed by the assembling skill from QuickBooks +
KPI-target `decision` events + the prior pack, exactly as board-pack §2 does
today. So this engine does NOT read events for KPI values — it takes the
substrate-derived KPI *readings* as input (the same numbers the pack computes)
and owns the DERIVATIONS (vs-target, direction, streak, watch) + the assembly.
The derivations are pure arithmetic over those readings — computed here, in
code, and rendered verbatim (the OUT7 fence). The caller never re-types a
derived number; it passes raw readings and renders what this returns.

This mirrors board-pack's posture precisely — the model assembles the raw
signal, the helper computes + renders — so the "byte-identical KPIs before and
after the factor-out" guarantee (SPEC OUT7 §6.2) is a golden over
`build_kpi_section`'s output for a fixed readings input.

NO CLOCK, NO I/O, NO EOS VOCAB
------------------------------
Pure over its inputs: no `datetime.now`, no file reads (the caller passes the
substrate-derived `data_through` date — the fence "every scorecard states its
data-through date" is enforced by `build_scorecard` REFUSING an empty one).
This keeps the engine deterministic + goldenable and immune to the
hardcoded-future-date time-bomb (G14). CORE surface: no EOS vocabulary
("rocks" / "L10" / "measurables") ever appears here — the EOS fork ships its
own scorecard concept; this one stays useful to every client (SPEC OUT7 §2).

Charts go through `charts.py` — the ONE chart owner (OUT3). The scorecard's
trend visuals build `line` / `bar_grouped` specs and hand them to make_brief's
`charts` slot; this module never emits a second SVG path (the stray-palette
guard enforces one chart owner).

Stdlib only.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Union

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# The kind this engine renders. Registered in brief_gates.EYEBROW_BY_KIND +
# STANDARD_KINDS and output_contract_validator.RULES_BY_KIND (same commit — the
# flip gotcha). make_brief(brief_kind=SCORECARD_KIND, ...) renders the .docx;
# premium_html renders the HTML when the profile says so.
SCORECARD_KIND = "kpi_scorecard"

# Mechanical warn band (SPEC OUT7 §4 — "threshold-mechanical, labeled as such;
# no editorializing dressed as data"). A KPI missing its target by up to this
# fraction of the target is "warn"; beyond it is "bad". On or past target in the
# good direction is "ok". This is the ONLY judgment in the module and it is a
# fixed threshold, never a model call.
WARN_BAND_FRAC = 0.05

# Tile-band cap — the top N KPIs as stat tiles above the table (components.py
# refuses >5; board-pack §2 uses "top 4–5"). Drop-empty: a KPI with no current
# value gets no tile.
TILE_BAND_CAP = 5

# "Needs attention" cap — the same 3 the exec-standard ask block caps at; a
# scorecard that flags more than three things has stopped triaging.
NEEDS_ATTENTION_CAP = 3

# Direction glyphs vs the PRIOR period (movement, not vs-target). Matches the
# board-pack tile arrow vocabulary so the two surfaces read as one system.
ARROW_UP = "▲"    # ▲
ARROW_DOWN = "▼"  # ▼
ARROW_FLAT = "▬"  # ▬

_PERIODS = ("month", "quarter")


class ScorecardInputError(ValueError):
    """A reading or a build argument violates the scorecard contract. Same
    ValueError contract as components.validate_tiles / charts.ChartDataError —
    the caller fixes the input, it never ships a half-built scorecard."""


# ---------------------------------------------------------------------------
# Formatting (deterministic; mirrors charts._fmt so the table, the tiles, and
# the chart axis all read the same value the same way)
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    """A finite number, or None. Bools are NOT numbers (charts._num posture)."""
    import math
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if math.isnan(v) or math.isinf(v):
        return None
    return float(v)


# Units that read better with a space before the suffix ("18.4 mo", "9 pts").
_SPACED_UNITS = frozenset({"mo", "pts", "pt", "days", "day", "h", "hrs", "wk"})


def format_value(v: Optional[float], unit: str = "") -> str:
    """Compact, deterministic KPI value label. `$` is a prefix with K/M/B
    scaling ("$478K"); `%` is a tight suffix ("134%"); mo/pts/days get a space
    ("18.4 mo"). None -> "" (the caller decides the drop-empty text). Mirrors
    charts._fmt so a value in the table equals the value on the chart axis."""
    n = _num(v)
    if n is None:
        return ""
    unit = unit or ""
    sign = "-" if n < 0 else ""
    a = abs(n)
    if unit == "$":
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
        return f"{sign}${s}"
    # Non-dollar: keep the raw magnitude (percent/points/counts read literally).
    if a == int(a):
        body = str(int(a))
    else:
        body = f"{a:.2f}".rstrip("0").rstrip(".")
    body = f"{sign}{body}"
    if not unit:
        return body
    if unit == "%":
        return f"{body}%"
    if unit in _SPACED_UNITS:
        return f"{body} {unit}"
    return f"{body}{unit}"


# ---------------------------------------------------------------------------
# The engine — compute_kpi_rows (pure; the ONE KPI computation)
# ---------------------------------------------------------------------------

def _good_gap(current: float, target: float, higher_is_better: bool) -> float:
    """Signed distance in the GOOD direction: >= 0 means on/above target."""
    return (current - target) if higher_is_better else (target - current)


def _status_of(current: Optional[float], target: Optional[float],
               higher_is_better: bool) -> str:
    """'ok' / 'warn' / 'bad' / 'none' — mechanical, one fixed threshold band.
    'none' when either value is missing (no target to grade against)."""
    c, t = _num(current), _num(target)
    if c is None or t is None:
        return "none"
    gap = _good_gap(c, t, higher_is_better)
    if gap >= 0:
        return "ok"
    denom = abs(t) if t != 0 else max(abs(c), 1.0)
    miss = abs(gap) / denom
    return "warn" if miss <= WARN_BAND_FRAC else "bad"


def _vs_target_label(current: Optional[float], target: Optional[float],
                     unit: str, higher_is_better: bool) -> str:
    """The Δ cell: signed distance from target in the KPI's own terms.
    Points/percent units show absolute point delta ("+9 pts"); other units show
    a percent-of-target delta ("+1.7%"). "on target" when it exactly meets it.
    "" when there is no target (never a blank cell — the caller renders a
    dash)."""
    c, t = _num(current), _num(target)
    if c is None or t is None:
        return ""
    raw = c - t
    if raw == 0:
        return "on target"
    sign = "+" if raw > 0 else "-"
    if unit in ("%", "pts", "pt"):
        mag = format_value(abs(raw), "pts")
        return f"{sign}{mag}"
    if t != 0:
        pct = abs(raw) / abs(t) * 100.0
        pct_s = f"{pct:.1f}".rstrip("0").rstrip(".")
        return f"{sign}{pct_s}%"
    return f"{sign}{format_value(abs(raw), unit)}"


def _direction(current: Optional[float], prior: Optional[float]) -> str:
    """Movement glyph vs the PRIOR period's value (not vs target). Flat when
    there is no prior or the value is unchanged."""
    c, p = _num(current), _num(prior)
    if c is None or p is None:
        return ARROW_FLAT
    if c > p:
        return ARROW_UP
    if c < p:
        return ARROW_DOWN
    return ARROW_FLAT


def _streak(current: Optional[float], target: Optional[float],
            history: Sequence[dict], higher_is_better: bool) -> dict:
    """Consecutive periods (through this one) on the SAME side of target.
    Counts backward from the current period while the on/off-target sign holds.
    Returns {"count", "above" (bool|None), "label"}. `history` is prior periods
    oldest->newest, each {current, target?}."""
    cur_status = _status_of(current, target, higher_is_better)
    if cur_status == "none":
        return {"count": 0, "above": None, "label": "—"}
    above = cur_status == "ok"
    count = 1
    for prev in reversed(list(history or [])):
        if not isinstance(prev, dict):
            break
        p_cur = prev.get("current")
        p_tgt = prev.get("target", target)
        p_status = _status_of(p_cur, p_tgt, higher_is_better)
        if p_status == "none":
            break
        if (p_status == "ok") == above:
            count += 1
        else:
            break
    # Plain-English side label ("3 on" / "2 off") — a glyph here would collide
    # with the tile band's numeric target-delta arrows and the Trend column's
    # movement arrows, and for a lower-is-better KPI "▲" would misread.
    side = "on" if above else "off"
    return {"count": count, "above": above, "label": f"{count} {side}"}


def _watch(current: Optional[float], target: Optional[float],
           history: Sequence[dict], higher_is_better: bool) -> dict:
    """The one-line watch flag: True when the KPI crossed from ON target (last
    period green) to OFF target (this period warn/bad) — a mechanical green->
    warn crossing, labeled as such. No prior period, or no crossing, -> False.
    Returns {"flag" (bool), "reason" (str)}."""
    cur_status = _status_of(current, target, higher_is_better)
    if cur_status in ("ok", "none"):
        return {"flag": False, "reason": ""}
    # Find the most recent gradable prior period.
    prior_status = "none"
    for prev in reversed(list(history or [])):
        if not isinstance(prev, dict):
            continue
        prior_status = _status_of(
            prev.get("current"), prev.get("target", target), higher_is_better)
        if prior_status != "none":
            break
    if prior_status == "ok":
        if cur_status == "warn":
            band = "into the warn band"
        else:
            # Direction-aware: a lower-is-better KPI (burn, churn) misses by
            # rising ABOVE its target — "below target" would misdescribe it.
            band = "below target" if higher_is_better else "above target"
        return {"flag": True,
                "reason": f"crossed {band} this period (was on target last period)"}
    return {"flag": False, "reason": ""}


def _coerce_reading(reading: dict, idx: int) -> dict:
    if not isinstance(reading, dict):
        raise ScorecardInputError(f"reading {idx} is not a dict: {reading!r}")
    name = reading.get("name")
    if not (isinstance(name, str) and name.strip()):
        raise ScorecardInputError(f"reading {idx} missing a 'name'")
    unit = reading.get("unit") or ""
    if not isinstance(unit, str):
        raise ScorecardInputError(f"reading {idx} 'unit' must be a string")
    history = reading.get("history") or []
    if not isinstance(history, list):
        raise ScorecardInputError(f"reading {idx} 'history' must be a list")
    return {
        "name": name.strip(),
        "unit": unit,
        "current": reading.get("current"),
        "target": reading.get("target"),
        "higher_is_better": reading.get("higher_is_better", True) is not False,
        "history": history,
    }


def compute_kpi_rows(readings: Sequence[dict]) -> List[dict]:
    """THE KPI computation — pure, deterministic, the one implementation both
    board-pack §2 and the scorecard consume. Turns substrate-derived readings
    into fully-derived rows. Never re-derives a number downstream; the display
    strings here ARE what renders.

    Each input reading (all numbers substrate-derived, passed by the caller):
        {name, unit, current: num|None, target: num|None,
         higher_is_better: bool=True,
         history: [{current: num, target?: num}]  # prior periods, old->new}

    Each output row:
        {name, unit, higher_is_better,
         current, current_disp, target, target_disp,
         vs_target,            # the Δ cell ("+1.7%" / "+9 pts" / "on target" / "")
         direction,            # ▲/▼/▬ vs prior period
         status,               # ok / warn / bad / none
         streak: {count, above, label},
         watch:  {flag, reason},
         prior_current, prior_current_disp}

    Order is preserved. Raises ScorecardInputError on a malformed reading."""
    rows: List[dict] = []
    for idx, raw in enumerate(readings or []):
        r = _coerce_reading(raw, idx)
        unit = r["unit"]
        hib = r["higher_is_better"]
        current, target, history = r["current"], r["target"], r["history"]
        prior_current = None
        for prev in reversed(history):
            if isinstance(prev, dict) and _num(prev.get("current")) is not None:
                prior_current = prev.get("current")
                break
        rows.append({
            "name": r["name"],
            "unit": unit,
            "higher_is_better": hib,
            "current": current,
            "current_disp": format_value(current, unit),
            "target": target,
            "target_disp": format_value(target, unit),
            "vs_target": _vs_target_label(current, target, unit, hib),
            "direction": _direction(current, prior_current),
            "status": _status_of(current, target, hib),
            "streak": _streak(current, target, history, hib),
            "watch": _watch(current, target, history, hib),
            "prior_current": prior_current,
            "prior_current_disp": format_value(prior_current, unit),
        })
    return rows


# ---------------------------------------------------------------------------
# Tile band + charts (shared building blocks)
# ---------------------------------------------------------------------------

def _tile_arrow(row: dict) -> str:
    """Target-delta arrow for a tile label (board-pack §2 vocabulary): ▲ above
    target, ▼ below, ▬ on target, and no arrow when there is no target.

    NUMERIC direction, matching the Δ cell's sign — never goodness (for a
    lower-is-better KPI, goodness lives in status/watch; an arrow that said
    "▲" for a churn number sitting BELOW target would misread as "churn up").
    """
    c, t = _num(row.get("current")), _num(row.get("target"))
    if c is None or t is None:
        return ""
    if c > t:
        return ARROW_UP
    if c < t:
        return ARROW_DOWN
    return ARROW_FLAT


def build_tile_band(rows: Sequence[dict], *, cap: int = TILE_BAND_CAP) -> List[dict]:
    """The top-`cap` KPIs as stat tiles (components.validate_tiles shape:
    [{label, value}], 1-5, empty REFUSED). Drop-empty: a KPI with no current
    value gets NO tile (never an empty frame); a real zero renders. Each tile's
    value is the current figure; the label is the KPI name with a target-delta
    arrow appended where a target exists — drawn from the SAME rows the table
    uses, never a second computation."""
    tiles: List[dict] = []
    for row in rows:
        if _num(row["current"]) is None:
            continue  # drop-empty
        arrow = _tile_arrow(row)
        label = f"{row['name']} {arrow}".strip() if arrow else row["name"]
        tiles.append({"label": label, "value": row["current_disp"]})
        if len(tiles) >= cap:
            break
    return tiles


def kpi_dollar_bar_spec(rows: Sequence[dict]) -> Optional[dict]:
    """A `bar_grouped` Current-vs-Target chart over the dollar KPIs — the SAME
    chart board-pack §2 attaches (SPEC OUT3). One unit per chart: only KPIs with
    unit '$' AND both a numeric current and target contribute (a mixed-scale
    chart renders the small series invisible — the table carries the rest).
    Returns a {kind, data, title} spec for charts.try_chart_png, or None when
    fewer than 2 dollar KPIs qualify (the caller keeps its table/tiles). This is
    a spec only — charts.py owns every pixel."""
    cats, cur, tgt = [], [], []
    for row in rows:
        if row["unit"] != "$":
            continue
        c, t = _num(row["current"]), _num(row["target"])
        if c is None or t is None:
            continue
        cats.append(row["name"])
        cur.append(c)
        tgt.append(t)
    if len(cats) < 2:
        return None
    return {
        "kind": "bar_grouped",
        "title": "KPIs vs targets",
        "data": {
            "categories": cats,
            "series": [
                {"label": "Current", "values": cur},
                {"label": "Target", "values": tgt},
            ],
            "unit": "$",
        },
    }


def kpi_trend_line_spec(row: dict) -> Optional[dict]:
    """A `line` trend of one KPI's history + current — the per-flagged-KPI trend
    visual (SPEC OUT7 §3b, "OUT3 trend line per flagged KPI when charts.py
    exists"). Builds a Value series (and a Target series when every point has a
    target) over the period sequence. Returns a {kind, data, title} spec, or
    None when there are fewer than 2 numeric points (charts refuses < 2). Spec
    only — charts.py renders it; there is no second SVG path.

    `row` is a computed row carrying its reading's `history` (prior periods,
    old->new) plus the current `current`/`target`; the points are those priors
    followed by the current period."""
    history = row.get("history") if isinstance(row.get("history"), list) else []
    points: List[dict] = []
    for prev in history:
        if isinstance(prev, dict) and _num(prev.get("current")) is not None:
            points.append({"current": prev.get("current"),
                           "target": prev.get("target")})
    if _num(row.get("current")) is not None:
        points.append({"current": row.get("current"),
                       "target": row.get("target")})
    value_pts = [{"x": f"P{i+1}", "y": _num(p.get("current"))}
                 for i, p in enumerate(points) if _num(p.get("current")) is not None]
    if len(value_pts) < 2:
        return None
    series = [{"label": row["name"], "points": value_pts}]
    tgt_pts = [{"x": f"P{i+1}", "y": _num(p.get("target"))}
               for i, p in enumerate(points) if _num(p.get("target")) is not None]
    if len(tgt_pts) == len(value_pts) and len(tgt_pts) >= 2:
        series.append({"label": "Target", "points": tgt_pts})
    spec = {"kind": "line", "title": f"{row['name']} trend",
            "data": {"series": series}}
    if row["unit"]:
        spec["data"]["unit"] = row["unit"]
    return spec


# ---------------------------------------------------------------------------
# Consumer 1 — the shared KPI section (board-pack §2's first caller)
# ---------------------------------------------------------------------------

_DASH = "—"  # em dash for an absent-but-legitimate cell


def build_kpi_section(
    readings: Sequence[dict],
    *,
    heading: str = "KPIs vs Targets",
    include_chart: bool = True,
    precomputed_rows: Optional[List[dict]] = None,
) -> dict:
    """The KPIs-vs-Targets SECTION — tile band + full table + the dollar
    Current-vs-Target bar. **This is the single KPI-section assembler; board-
    pack §2 is its first caller** (its SKILL.md builds §2 by calling this), so
    the pack and the standalone scorecard render KPIs from one implementation.

    Table columns: Metric | Current | Target | Vs target | Trend — no blank
    cells (an absent target renders the em dash, never empty: the board_pack
    table_no_blank_cells contract). `Trend` is the movement arrow vs the prior
    period. Deterministic over `readings`, so its output is goldenable (the
    OUT7 §6.2 byte-identical guarantee).

    Returns a make_brief section dict: {heading, tiles, table, charts?}. The
    chart is best-effort (charts.py drops it on this machine if it can't
    rasterize); the table is always the structural fallback."""
    rows = precomputed_rows if precomputed_rows is not None else compute_kpi_rows(readings)
    tiles = build_tile_band(rows)
    table_rows: List[list] = []
    for row in rows:
        current = row["current_disp"] or _DASH
        target = row["target_disp"] or _DASH
        vs = row["vs_target"] or _DASH
        table_rows.append([row["name"], current, target, vs, row["direction"]])
    section: dict = {
        "heading": heading,
        "table": {
            "headers": ["Metric", "Current", "Target", "Vs target", "Trend"],
            "rows": table_rows,
        },
    }
    if tiles:
        section["tiles"] = tiles
    if include_chart:
        chart = kpi_dollar_bar_spec(rows)
        if chart is not None:
            section["charts"] = [chart]
    return section


# ---------------------------------------------------------------------------
# Consumer 2 — the full scorecard / QBR pre-read
# ---------------------------------------------------------------------------

def _verdict_line(rows: Sequence[dict]) -> str:
    """The single most decision-relevant KPI move this period (SPEC OUT7 §3b
    exec-header rule). Mechanical ranking: a fresh watch crossing first, then
    the largest current miss, else the strongest KPI on target. Every clause
    traces to a computed field — no editorializing."""
    if not rows:
        return "No KPIs on the scorecard this period."
    watched = [r for r in rows if r["watch"]["flag"]]
    if watched:
        r = watched[0]
        return (f"{r['name']} {r['watch']['reason']} "
                f"{_DASH} {r['current_disp']} against a {r['target_disp']} target.")
    off = [r for r in rows if r["status"] in ("warn", "bad")]
    if off:
        # Largest miss by good-direction gap magnitude relative to target.
        def _miss(r):
            c, t = _num(r["current"]), _num(r["target"])
            if c is None or t is None:
                return 0.0
            denom = abs(t) if t != 0 else 1.0
            return abs(_good_gap(c, t, r["higher_is_better"])) / denom
        r = sorted(off, key=_miss, reverse=True)[0]
        # Direction-aware: churn/burn-class KPIs miss by sitting ABOVE target.
        side = "below" if r["higher_is_better"] else "above"
        return (f"{r['name']} is {side} target {_DASH} {r['current_disp']} "
                f"against {r['target_disp']} ({r['vs_target']}).")
    graded = [r for r in rows if r["status"] == "ok"]
    n = len(graded)
    if n:
        return (f"All {n} graded KPI{'s' if n != 1 else ''} on target or "
                f"better this period.")
    return "No KPIs have a target set this period."


def _needs_attention(rows: Sequence[dict]) -> List[str]:
    """Up to NEEDS_ATTENTION_CAP one-liners for the flagged KPIs (watch first,
    then bad, then warn), each stating its mechanical reason. [] when nothing is
    off target — the block renders '(every KPI on or above target)' upstream."""
    def _rank(r):
        if r["watch"]["flag"]:
            return 0
        return {"bad": 1, "warn": 2}.get(r["status"], 9)
    flagged = [r for r in rows if r["watch"]["flag"] or r["status"] in ("warn", "bad")]
    flagged.sort(key=lambda r: (_rank(r), r["name"]))
    lines: List[str] = []
    for r in flagged[:NEEDS_ATTENTION_CAP]:
        if r["watch"]["flag"]:
            lines.append(f"{r['name']}: {r['watch']['reason']} "
                         f"({r['current_disp']} vs {r['target_disp']}).")
        else:
            lines.append(f"{r['name']}: {r['current_disp']} vs {r['target_disp']} "
                         f"target ({r['vs_target']}).")
    return lines


def build_scorecard(
    readings: Sequence[dict],
    *,
    period: str = "month",
    data_through: str,
    org_name: str = "",
    title: Optional[str] = None,
    decisions: Optional[Sequence[str]] = None,
    prior_period_note: str = "",
) -> dict:
    """Assemble the full KPI scorecard payload for make_brief / make_premium_brief.

    Args:
      readings: substrate-derived KPI readings (see compute_kpi_rows). The
        numbers come from the caller (QuickBooks + KPI-target decision events +
        the prior pack) — this engine never re-types them, it derives from them.
      period: "month" (the default scorecard) or "quarter" (the QBR pre-read —
        same generator, adds a decisions-logged section + a prior-quarter note).
      data_through: the substrate date the numbers are current as of. REQUIRED
        and non-empty — the OUT7 fence "every scorecard states its data-through
        date" is enforced here (empty -> ScorecardInputError). Rendered in the
        subtitle. A plain date string the caller derives from the latest event;
        this module reads no clock.
      org_name: optional org label for the subtitle.
      title: optional title override (default "KPI Scorecard" / "QBR Pre-Read").
      decisions: quarter-only — one line per decision logged in the quarter
        (from `decision` events; the caller supplies them). Ignored for month.
      prior_period_note: quarter-only — a one-line prior-quarter comparison the
        caller derived (e.g. "MRR +18% vs Q1"). Rendered under the table.

    Returns {brief_kind, title, subtitle, sections, exec_header}. Pass these
    straight to make_brief(brief_kind=..., title=..., subtitle=...,
    sections=..., exec_header=...). Never pass `asks` — the needs-attention
    section is the scorecard's action surface, not a reader-ask block.

    Raises ScorecardInputError on an unknown period, an empty data_through, or a
    malformed reading."""
    if period not in _PERIODS:
        raise ScorecardInputError(
            f"period must be one of {_PERIODS}, got {period!r}")
    if not (isinstance(data_through, str) and data_through.strip()):
        raise ScorecardInputError(
            "data_through is required and non-empty — every scorecard states "
            "the date its numbers are current as of (SPEC OUT7 §4)")
    data_through = data_through.strip()
    is_quarter = period == "quarter"

    rows = compute_kpi_rows(readings)

    if title is None:
        title = "QBR Pre-Read" if is_quarter else "KPI Scorecard"
    period_word = "Quarterly" if is_quarter else "Monthly"
    subtitle_bits = [f"{period_word} KPI scorecard",
                     f"data through {data_through}"]
    if org_name.strip():
        subtitle_bits.append(org_name.strip())
    subtitle = " · ".join(subtitle_bits)

    sections: List[dict] = []

    # §1 — the KPI section (tile band + table + dollar bar), the shared assembler.
    kpi_section = build_kpi_section(readings, heading="KPIs vs Targets",
                                    precomputed_rows=rows)
    # Quarter-only (the documented contract): the note is a prior-QUARTER
    # comparison and the lead-in says so — rendering it on a monthly scorecard
    # would caption month numbers with quarter language.
    if is_quarter and prior_period_note.strip():
        note = f"Vs last quarter: {prior_period_note.strip()}"
        kpi_section["body"] = note
    sections.append(kpi_section)

    # §2 — the scorecard table proper: KPI · actual · target · Δ · streak · watch.
    detail_rows: List[list] = []
    for row in rows:
        actual = row["current_disp"] or _DASH
        if actual != _DASH and row["direction"]:
            actual = f"{actual} {row['direction']}"
        target = row["target_disp"] or _DASH
        delta = row["vs_target"] or _DASH
        streak = row["streak"]["label"]
        watch = "⚠" if row["watch"]["flag"] else _DASH  # ⚠
        detail_rows.append([row["name"], actual, target, delta, streak, watch])
    sections.append({
        "heading": "Scorecard",
        "table": {
            "headers": ["KPI", "Actual", "Target", "Δ", "Streak", "Watch"],
            "rows": detail_rows,
        },
    })

    # §3 — trend line per FLAGGED KPI (watch or off target), via charts.py only.
    trend_specs: List[dict] = []
    for reading, row in zip(readings, rows):
        if not (row["watch"]["flag"] or row["status"] in ("warn", "bad")):
            continue
        merged = dict(row)
        merged["history"] = reading.get("history") if isinstance(
            reading, dict) else []
        spec = kpi_trend_line_spec(merged)
        if spec is not None:
            trend_specs.append(spec)
    if trend_specs:
        sections.append({
            "heading": "Trends for flagged KPIs",
            "body": ("Period-over-period trend for each KPI flagged below. "
                     "Where this machine can render charts they appear here; "
                     "the scorecard table above is the complete record either way."),
            "charts": trend_specs,
        })

    # §4 (quarter only) — decisions logged this quarter.
    if is_quarter:
        dec_lines = [d.strip() for d in (decisions or []) if isinstance(d, str) and d.strip()]
        sections.append({
            "heading": "Decisions logged this quarter",
            "bullets": dec_lines if dec_lines else ["(no decisions logged this quarter)"],
        })

    # §5 — needs attention (<=3), the scorecard's action surface.
    attention = _needs_attention(rows)
    sections.append({
        "heading": "Needs attention",
        "bullets": attention if attention else ["(every KPI on target or better this period)"],
    })

    exec_header = {
        "verdict": _verdict_line(rows),
        "changed": _changed_line(rows),
        "decide": _decide_line(rows),
        "needs": _needs_line(attention),
    }

    return {
        "brief_kind": SCORECARD_KIND,
        "title": title,
        "subtitle": subtitle,
        "sections": sections,
        "exec_header": exec_header,
    }


def _changed_line(rows: Sequence[dict]) -> str:
    """CHANGED eyebrow: the count of KPIs that moved up vs down vs prior."""
    up = sum(1 for r in rows if r["direction"] == ARROW_UP)
    down = sum(1 for r in rows if r["direction"] == ARROW_DOWN)
    graded = [r for r in rows if r["status"] != "none"]
    on = sum(1 for r in graded if r["status"] == "ok")
    if not rows:
        return "No KPIs on the scorecard."
    return (f"{up} KPI{'s' if up != 1 else ''} up, {down} down vs last period; "
            f"{on}/{len(graded)} on target or better.")


def _decide_line(rows: Sequence[dict]) -> str:
    """DECIDE eyebrow: names the off-target KPIs needing a call, or steady-state."""
    off = [r["name"] for r in rows if r["status"] in ("warn", "bad")]
    if not off:
        return "Nothing forced this period — every graded KPI is on track."
    if len(off) == 1:
        return f"Whether {off[0]} needs an intervention this period."
    shown = ", ".join(off[:3])
    more = "" if len(off) <= 3 else f" (+{len(off) - 3} more)"
    return f"Which of these to act on: {shown}{more}."


def _needs_line(attention: Sequence[str]) -> str:
    """NEEDED eyebrow: mirrors the needs-attention count (anti-washing floor —
    a real 'nothing' when nothing is flagged)."""
    n = len(attention)
    if not n:
        return "Nothing flagged — no KPI crossed off target this period."
    return f"Eyes on {n} flagged KPI{'s' if n != 1 else ''} (detail below)."


__all__ = [
    "SCORECARD_KIND",
    "WARN_BAND_FRAC",
    "TILE_BAND_CAP",
    "NEEDS_ATTENTION_CAP",
    "ARROW_UP",
    "ARROW_DOWN",
    "ARROW_FLAT",
    "ScorecardInputError",
    "format_value",
    "compute_kpi_rows",
    "build_tile_band",
    "kpi_dollar_bar_spec",
    "kpi_trend_line_spec",
    "build_kpi_section",
    "build_scorecard",
]
