#!/usr/bin/env python3
"""
deal_health.py — per-stage rot flags over OBSERVED activity (SPEC PIPE1, D7).

Deal staleness is stage-dependent (a lead can sit 10 quiet days; a
negotiation can't), so stalled-projects' project-generic thresholds don't
apply — kind='deal' threads are fenced OUT of that scan and report here
instead. Recency comes from thread_activity.derive_thread_activity (events:
meetings, interactions, commitments on the thread) — NEVER the deprecated
thread.last_activity stamp. This beats a field-edit rot timer (the Pipedrive
shape, which resets on any edit) because it keys on real contact, which only
the connector substrate can see.

Independent flags per open deal (any subset can fire):

  no_next_step       zero OPEN commitments on the deal thread (D3 — the
                     deal's next step IS a commitment; a dated next step is
                     the single strongest predictor of closing)
  close_date_passed  expected_close is behind today
  rotting            observed quiet-days exceed the stage threshold
  zombie             deal age exceeds 2× the median won-cycle once ≥ 5 wins
                     exist, else the 90-day default. Auto-PROPOSES a
                     close-out — never auto-closes.

Pure functions over caller-supplied data (no I/O in the math; the caller
derives activity + open commitments once per fire). stdlib only.
"""
from __future__ import annotations

import datetime
from typing import Optional

# Per-stage quiet-day thresholds (FRP1-tunable; these are the defaults the
# first-run block saves).
DEFAULT_STAGE_THRESHOLDS = {
    "lead": 10,
    "qualified": 10,
    "proposal_sent": 7,
    "negotiating": 7,
}

# Zombie age floor until the workspace has enough won-cycle history.
ZOMBIE_DEFAULT_DAYS = 90
ZOMBIE_MIN_WINS = 5
ZOMBIE_CYCLE_MULTIPLIER = 2


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


def median_won_cycle_days(closed_deals: list[dict]) -> Optional[int]:
    """Median (opened_at → closed_at) days across WON deals. None until
    ZOMBIE_MIN_WINS wins with parseable dates exist — callers fall back to
    ZOMBIE_DEFAULT_DAYS. Even-count median = mean of the middle pair,
    rounded half-up to a whole day."""
    cycles: list[int] = []
    for row in closed_deals or []:
        deal = row.get("deal") if isinstance(row, dict) else None
        if not isinstance(deal, dict) or deal.get("outcome") != "won":
            continue
        opened = _as_date(deal.get("opened_at"))
        closed = _as_date(deal.get("closed_at"))
        if opened and closed and closed >= opened:
            cycles.append((closed - opened).days)
    if len(cycles) < ZOMBIE_MIN_WINS:
        return None
    cycles.sort()
    n = len(cycles)
    if n % 2:
        return cycles[n // 2]
    return int((cycles[n // 2 - 1] + cycles[n // 2] + 1) // 2)


def zombie_threshold_days(closed_deals: list[dict]) -> int:
    """2× the median won-cycle once enough wins exist; 90d default before."""
    median = median_won_cycle_days(closed_deals)
    if median is None:
        return ZOMBIE_DEFAULT_DAYS
    return ZOMBIE_CYCLE_MULTIPLIER * median


def compute_deal_health(
    open_deals: list[dict],
    *,
    activity_by_thread: dict,
    open_commitment_thread_ids: set,
    today,
    stage_thresholds: Optional[dict] = None,
    zombie_days: int = ZOMBIE_DEFAULT_DAYS,
) -> list[dict]:
    """Health row per open deal (pure — no I/O, no estimation).

    Args:
      open_deals: deal_state.list_open_deals rows. Untracked rows (no deal
        object) pass through with flags=[] and health untouched — the
        surface renders them as adoption offers, not rot alarms.
      activity_by_thread: thread_activity.derive_thread_activity output
        ({thread_id: ThreadActivity}) — THE recency source (F-54: never the
        stored last_activity field).
      open_commitment_thread_ids: {primary_thread_id of every OPEN
        commitment} from cru_match.load_open_commitments — the D3
        next-step signal.
      today: date or ISO string (caller passes it — keeps this testable;
        never call date.today() in here).
      stage_thresholds: per-stage quiet-day thresholds (defaults above).
      zombie_days: zombie_threshold_days(closed_deals) from the caller.

    Returns one dict per input row:
      {thread_id, name, org_id, deal, untracked, stage, value,
       days_quiet (int|None), days_in_stage (int|None), age_days (int|None),
       flags: [no_next_step|close_date_passed|rotting|zombie, ...],
       severity: int}
    """
    thresholds = dict(DEFAULT_STAGE_THRESHOLDS)
    if stage_thresholds:
        thresholds.update(stage_thresholds)
    today_d = _as_date(today)
    if today_d is None:
        raise ValueError(f"compute_deal_health needs a parseable today, got {today!r}")

    rows: list[dict] = []
    for row in open_deals or []:
        thread_id = row.get("thread_id")
        deal = row.get("deal") if isinstance(row.get("deal"), dict) else None
        out = {
            "thread_id": thread_id,
            "name": row.get("name"),
            "org_id": row.get("org_id"),
            "deal": deal,
            "untracked": bool(row.get("untracked")),
            "stage": (deal or {}).get("stage"),
            "value": (deal or {}).get("value"),
            "days_quiet": None,
            "days_in_stage": None,
            "age_days": None,
            "flags": [],
            "severity": 0,
        }
        if deal is None:
            # Pre-PIPE1 untracked deal thread — offer adoption, never rot math.
            rows.append(out)
            continue

        act = activity_by_thread.get(thread_id)
        act_date = _as_date(getattr(act, "ts", None)) if act is not None else None
        baseline = act_date or _as_date(deal.get("opened_at"))
        if baseline is not None:
            out["days_quiet"] = max(0, (today_d - baseline).days)
        entered = _as_date(deal.get("stage_entered"))
        if entered is not None:
            out["days_in_stage"] = max(0, (today_d - entered).days)
        opened = _as_date(deal.get("opened_at"))
        if opened is not None:
            out["age_days"] = max(0, (today_d - opened).days)

        flags = []
        if thread_id not in open_commitment_thread_ids:
            flags.append("no_next_step")
        expected = _as_date(deal.get("expected_close"))
        if expected is not None and expected < today_d:
            flags.append("close_date_passed")
        threshold = thresholds.get(out["stage"] or "", None)
        if threshold is not None and out["days_quiet"] is not None \
                and out["days_quiet"] > threshold:
            flags.append("rotting")
        if out["age_days"] is not None and out["age_days"] > zombie_days:
            flags.append("zombie")

        out["flags"] = flags
        out["severity"] = severity_points(flags)
        rows.append(out)
    return rows


# Severity weights — the ranking input pipeline_math blends with value.
# Hand-tuned constants, asserted exactly in tests; change them there too.
SEVERITY_POINTS = {
    "zombie": 4,
    "close_date_passed": 3,
    "rotting": 3,
    "no_next_step": 2,
}


def severity_points(flags: list[str]) -> int:
    return sum(SEVERITY_POINTS.get(f, 0) for f in flags or [])


__all__ = [
    "DEFAULT_STAGE_THRESHOLDS",
    "ZOMBIE_DEFAULT_DAYS",
    "ZOMBIE_MIN_WINS",
    "ZOMBIE_CYCLE_MULTIPLIER",
    "SEVERITY_POINTS",
    "median_won_cycle_days",
    "zombie_threshold_days",
    "compute_deal_health",
    "severity_points",
]
