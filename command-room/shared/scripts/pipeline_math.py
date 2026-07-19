#!/usr/bin/env python3
"""
pipeline_math.py — tile numbers + ranking for the pipeline surface
(SPEC PIPE1, D8). ALL arithmetic the pipeline report renders lives here —
never re-derived in skill prose (Bug #99 class; the ranking-in-code
discipline relationship_moves.py set). Hand-computed test vectors in
tests/run_pipeline_math_test.py assert exact values, no tolerance.

No estimation anywhere: a deal without a stated value contributes nothing
to any dollar figure; a tile whose datum can't be derived is DROPPED
(F-60 drop-empty), never rendered as $0 or a guess.

Pure functions over caller-supplied rows. stdlib only.
"""
from __future__ import annotations

import datetime
from typing import Optional

from deal_health import severity_points  # noqa: F401  (re-export for callers)

# Won-rate needs a minimum sample or it misleads (a 1-for-1 quarter renders
# 100%): below this many terminal events in the window, the tile drops.
WON_RATE_MIN_TERMINAL = 4
WON_RATE_WINDOW_DAYS = 90

# Categorical haircut weights (D2: forecast_category is the forecasting
# vocabulary; weighted-pipeline math is meaningless at tiny N, so the
# haircut renders as a SECONDARY tile at most, and only when >=1 open deal
# carries a category). Deals with no category are excluded, not guessed.
HAIRCUT_WEIGHTS = {"commit": 0.9, "best_case": 0.6, "pipeline": 0.3}

# Ranking blend: severity is the driver, value breaks ties upward so a
# $100k rotting deal outranks a $2k rotting deal. score = severity * 1000
# + min(value, 999_000)/1000 (integer math — exact in tests). Unflagged
# deals (severity 0) rank purely by value; stable tie-break by days_quiet
# then name is applied in rank_deals.
_VALUE_CAP = 999_000


def _as_date(value) -> Optional[datetime.date]:
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


def _deal_value(row: dict) -> Optional[float]:
    deal = row.get("deal") if isinstance(row.get("deal"), dict) else None
    if not deal:
        return None
    v = deal.get("value")
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        return None
    return float(v)


def open_pipeline_value(open_deals: list[dict]) -> Optional[float]:
    """Sum of stated values across open tracked deals. None when NO open
    deal carries a value (drop the tile — never $0-frame a pipeline that
    simply hasn't been priced)."""
    values = [_deal_value(r) for r in open_deals or []]
    values = [v for v in values if v is not None]
    if not values:
        return None
    return float(sum(values))


def closing_this_month(open_deals: list[dict], today) -> tuple[int, float]:
    """(count, summed value) of open deals whose expected_close falls inside
    today's calendar month. Deals without an expected_close don't count;
    deals without a value count toward n but contribute 0 to the sum."""
    today_d = _as_date(today)
    if today_d is None:
        raise ValueError(f"closing_this_month needs a parseable today, got {today!r}")
    n = 0
    total = 0.0
    for row in open_deals or []:
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else None
        if not deal:
            continue
        expected = _as_date(deal.get("expected_close"))
        if expected is None:
            continue
        if expected.year == today_d.year and expected.month == today_d.month:
            n += 1
            v = _deal_value(row)
            if v is not None:
                total += v
    return n, total


_STALL_FLAGS = frozenset({"rotting", "no_next_step"})


def stalled_count(health_rows: list[dict]) -> int:
    """Distinct open deals that are STALLED — rotting (stage-threshold quiet)
    or missing a next step. Zombie and close_date_passed are their own
    buckets (a zombie auto-proposes close-out; a passed close date is a
    forecast miss, not rot) — they rank a deal up but don't inflate the
    stalled tile. One deal with both stall flags counts once."""
    return sum(1 for r in health_rows or []
               if _STALL_FLAGS & set(r.get("flags") or ()))


def won_rate_90d(deal_events: list[dict], today,
                 *, window_days: int = WON_RATE_WINDOW_DAYS,
                 min_terminal: int = WON_RATE_MIN_TERMINAL) -> Optional[float]:
    """wins / (wins + losses) over deal_won + deal_lost events inside the
    window. None (drop the tile) below `min_terminal` terminal events —
    a 2-for-2 quarter must not render a misleading 100%."""
    today_d = _as_date(today)
    if today_d is None:
        raise ValueError(f"won_rate_90d needs a parseable today, got {today!r}")
    floor = today_d - datetime.timedelta(days=window_days)
    wins = losses = 0
    for ev in deal_events or []:
        et = ev.get("type")
        if et not in ("deal_won", "deal_lost"):
            continue
        ts = _as_date(ev.get("ts"))
        if ts is None or ts < floor or ts > today_d:
            continue
        if et == "deal_won":
            wins += 1
        else:
            losses += 1
    total = wins + losses
    if total < min_terminal:
        return None
    return wins / total


def haircut_value(open_deals: list[dict]) -> Optional[float]:
    """Category-weighted pipeline value. None unless >=1 open deal carries
    BOTH a value and a forecast_category — the secondary tile renders only
    then (D2). Uncategorized/unvalued deals are excluded, never guessed."""
    total = 0.0
    any_row = False
    for row in open_deals or []:
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else None
        if not deal:
            continue
        weight = HAIRCUT_WEIGHTS.get(deal.get("forecast_category") or "")
        value = _deal_value(row)
        if weight is None or value is None:
            continue
        total += weight * value
        any_row = True
    return total if any_row else None


def rank_score(health_row: dict) -> int:
    """Deterministic ranking score: severity dominates, value breaks ties
    upward. Integer math — exact in tests."""
    severity = int(health_row.get("severity") or 0)
    value = _deal_value(health_row) or 0.0
    return severity * 1000 + int(min(value, _VALUE_CAP) / 1000)


def rank_deals(health_rows: list[dict]) -> list[dict]:
    """Health rows sorted for the ranked report: score desc, then days_quiet
    desc, then name asc (stable, fully deterministic). Untracked rows sink
    to the bottom (they carry no health math — they render as adoption
    offers)."""
    def key(r):
        return (
            0 if not r.get("untracked") else 1,
            -rank_score(r),
            -(r.get("days_quiet") if isinstance(r.get("days_quiet"), int) else -1),
            str(r.get("name") or ""),
        )
    return sorted(health_rows or [], key=key)


def _fmt_money(n: float) -> str:
    """$40K / $240K / $1.2M — same shape quantify uses. Only called on
    derived positive sums."""
    if n >= 1_000_000:
        m = n / 1_000_000
        return f"${m:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"${round(n / 1000)}K"
    return f"${int(round(n))}"


def pipeline_tiles(open_deals: list[dict], health_rows: list[dict],
                   deal_events: list[dict], today) -> list[dict]:
    """The tile band for the pipeline report, drop-empty (F-60): each tile
    appears only when its datum derives from the substrate. Shape matches
    components.validate_tiles ({label, value}); 0 open deals returns [] and
    the surface skips the band entirely.

      Open pipeline $        sum of stated open-deal values
      Closing this month     n · $sum (n>0)
      Stalled                count of flagged deals (real zero renders)
      Won rate 90d           wins/(wins+losses), >= 4 terminal events
      Weighted $             haircut, only when >=1 categorized deal
    """
    if not open_deals:
        return []
    tiles: list[dict] = []
    open_value = open_pipeline_value(open_deals)
    if open_value is not None:
        tiles.append({"label": "Open pipeline", "value": _fmt_money(open_value)})
    n_closing, closing_value = closing_this_month(open_deals, today)
    if n_closing > 0:
        val = f"{n_closing}"
        if closing_value > 0:
            val += f" · {_fmt_money(closing_value)}"
        tiles.append({"label": "Closing this month", "value": val})
    # Stalled: a real zero is information ("nothing is rotting") — renders.
    tiles.append({"label": "Stalled", "value": str(stalled_count(health_rows))})
    rate = won_rate_90d(deal_events, today)
    if rate is not None:
        tiles.append({"label": "Won rate 90d", "value": f"{round(rate * 100)}%"})
    haircut = haircut_value(open_deals)
    if haircut is not None and len(tiles) < 5:
        tiles.append({"label": "Weighted", "value": _fmt_money(haircut)})
    return tiles


__all__ = [
    "WON_RATE_MIN_TERMINAL",
    "WON_RATE_WINDOW_DAYS",
    "HAIRCUT_WEIGHTS",
    "open_pipeline_value",
    "closing_this_month",
    "stalled_count",
    "won_rate_90d",
    "haircut_value",
    "rank_score",
    "rank_deals",
    "pipeline_tiles",
    "severity_points",
]
