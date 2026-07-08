#!/usr/bin/env python3
"""
Loop 4 — confidence-threshold calibration (Phase 6, Round 2).

The CRU match-score thresholds (`MATCH_SCORE_AUTO_RESOLVE` = 0.55,
`MATCH_SCORE_PENDING_REVIEW` = 0.30 in `confidence.py`) are one-size-fits-all
constants. Meanwhile every workspace records exactly how accurate its own bands
are: a `commitment_review_proposed` event carries the `match_score` that put an
item in the pending band, and the CEO's later `resolved` (→ `commitment_resolved`)
or `not relevant` (→ `commitment_review_dismissed`) says whether that band was
right. This pass reads those outcomes, computes the confirm-rate per band, and —
when a band is consistently right over a small-n floor — proposes a per-workspace
override that `confidence.py`'s accessors read (`_hq/data/confidence-overrides.json`).

Discipline (mirrors Pass 7b's small-n honesty + Pass 9/10's cooldown):
  - **≥20 terminal outcomes** in a band before it can move a threshold (a band
    confirmed 100% off n=3 is noise).
  - **One proposal max per run** — a single dial move at a time, user-approved
    through the review widget, never a silent change.
  - A declined proposal enters the shared 60-day cooldown (`proposal_ledger`).

Two directions:
  - LOOSEN: a pending sub-band (0.30–0.55) confirmed ≥95% over ≥20 → propose
    lowering `MATCH_SCORE_AUTO_RESOLVE` to that band's floor (it auto-resolves).
  - TIGHTEN: auto-resolves that get reversed (`commitment_reopened` after a CRU
    auto-resolve) above a small rate → propose raising `MATCH_SCORE_AUTO_RESOLVE`.

Pure scoring helpers take no clock and do no I/O. stdlib only.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Optional

try:
    from events_io import iter_events
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from events_io import iter_events  # type: ignore

MIN_SAMPLES = 20          # small-n floor per band (Loop 4 spec: ≥20)
LOOSEN_RATE = 0.95        # confirm-rate to justify lowering auto-resolve
TIGHTEN_REVERSAL_RATE = 0.10  # reversal-rate to justify raising auto-resolve
PASS_NAME = "loop4_confidence_calibration"

# Pending-band sub-bands scored for the loosen direction (floor of each is a
# candidate new auto-resolve threshold).
DEFAULT_BANDS = ((0.475, 0.55), (0.40, 0.475), (0.30, 0.40))


def load_review_outcomes(workspace_root) -> List[dict]:
    """Terminal outcomes for CRU review proposals: one row per
    `commitment_review_proposed` that later resolved or was dismissed.
    Returns {commitment_id, match_score, outcome: 'confirmed'|'dismissed',
    reopened_after_resolve: bool}. Never raises."""
    proposed: Dict[str, dict] = {}
    resolved: set = set()
    dismissed: set = set()
    reopened: set = set()
    try:
        events = list(iter_events(Path(workspace_root) / "_hq" / "data"))
    except Exception:
        return []
    for ev in events:
        t = ev.get("type")
        data = ev.get("data") or {}
        cid = data.get("commitment_id") or data.get("id")
        if t == "commitment_review_proposed" and cid:
            score = data.get("match_score")
            if isinstance(score, (int, float)):
                # keep the FIRST proposal per commitment (its original band)
                proposed.setdefault(cid, {"commitment_id": cid,
                                          "match_score": float(score)})
        elif t == "commitment_resolved" and cid:
            resolved.add(cid)
        elif t == "commitment_review_dismissed" and cid:
            dismissed.add(cid)
        elif t == "commitment_reopened" and cid:
            reopened.add(cid)

    out: List[dict] = []
    for cid, row in proposed.items():
        if cid in resolved:
            outcome = "confirmed"
        elif cid in dismissed:
            outcome = "dismissed"
        else:
            continue  # still pending — not terminal, exclude
        out.append({**row, "outcome": outcome,
                    "reopened_after_resolve": cid in reopened})
    return out


def confirm_rate_by_band(outcomes: List[dict], bands=DEFAULT_BANDS) -> Dict[tuple, dict]:
    """Confirm-rate per pending sub-band. Returns {band: {n, confirmed, rate}}.
    Pure."""
    stats = {b: {"n": 0, "confirmed": 0, "rate": 0.0} for b in bands}
    for o in outcomes:
        s = o.get("match_score")
        if s is None:
            continue
        for lo, hi in bands:
            if lo <= s < hi:
                slot = stats[(lo, hi)]
                slot["n"] += 1
                if o.get("outcome") == "confirmed":
                    slot["confirmed"] += 1
                break
    for b, slot in stats.items():
        slot["rate"] = (slot["confirmed"] / slot["n"]) if slot["n"] else 0.0
    return stats


def reversal_rate(outcomes: List[dict]) -> tuple:
    """(reversed, confirmed_total, rate) — of the confirmed (auto-resolved-ish)
    outcomes, how many were later reopened. Pure."""
    confirmed = [o for o in outcomes if o.get("outcome") == "confirmed"]
    rev = sum(1 for o in confirmed if o.get("reopened_after_resolve"))
    total = len(confirmed)
    return rev, total, (rev / total if total else 0.0)


def _fp(threshold_name: str, direction: str, proposed_value: float) -> str:
    raw = f"{threshold_name}\x00{direction}\x00{round(proposed_value, 3)}"
    return "cal_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def propose_calibration(
    outcomes: List[dict],
    *,
    current_auto_resolve: float,
    cooldown_fingerprints: Optional[set] = None,
    min_samples: int = MIN_SAMPLES,
) -> List[dict]:
    """At most ONE calibration proposal (Loop 4 spec). Pure. Returns
    [] or [{fingerprint, threshold_name, direction, current, proposed, band, n,
    confirm_rate, plain}]."""
    cooling = cooldown_fingerprints or set()

    # TIGHTEN first — a reversal problem is more urgent than a missed loosen.
    rev, tot, rrate = reversal_rate(outcomes)
    if tot >= min_samples and rrate > TIGHTEN_REVERSAL_RATE:
        proposed = round(min(0.95, current_auto_resolve + 0.05), 3)
        fp = _fp("MATCH_SCORE_AUTO_RESOLVE", "tighten", proposed)
        if fp not in cooling and proposed > current_auto_resolve:
            return [{
                "fingerprint": fp, "threshold_name": "MATCH_SCORE_AUTO_RESOLVE",
                "direction": "tighten", "current": current_auto_resolve,
                "proposed": proposed, "band": None, "n": tot,
                "confirm_rate": round(1 - rrate, 3),
                "plain": (f"About {round(rrate * 100)}% of the matches I auto-closed "
                          f"got reopened — want me to be more careful before "
                          f"auto-closing?"),
            }]

    # LOOSEN — the highest sub-band that clears the floor + rate.
    stats = confirm_rate_by_band(outcomes)
    for (lo, hi), slot in sorted(stats.items(), key=lambda kv: -kv[0][0]):
        if slot["n"] >= min_samples and slot["rate"] >= LOOSEN_RATE and lo < current_auto_resolve:
            fp = _fp("MATCH_SCORE_AUTO_RESOLVE", "loosen", lo)
            if fp in cooling:
                continue
            return [{
                "fingerprint": fp, "threshold_name": "MATCH_SCORE_AUTO_RESOLVE",
                "direction": "loosen", "current": current_auto_resolve,
                "proposed": round(lo, 3), "band": (lo, hi), "n": slot["n"],
                "confirm_rate": round(slot["rate"], 3),
                "plain": (f"You've confirmed {round(slot['rate'] * 100)}% of the "
                          f"{slot['n']} 'likely' matches I flagged for review — "
                          f"want me to just auto-close that strong a match instead "
                          f"of asking?"),
            }]
    return []


def apply_calibration(workspace_root, proposal: dict) -> Optional[float]:
    """Persist an APPROVED calibration proposal into
    `_hq/data/confidence-overrides.json` (merging with any existing overrides).
    Returns the new value, or None on error. Never raises."""
    try:
        import confidence
        existing = confidence.load_overrides(workspace_root)
        existing[proposal["threshold_name"]] = float(proposal["proposed"])
        confidence.write_overrides(workspace_root, existing)
        return float(proposal["proposed"])
    except Exception:
        return None


__all__ = [
    "MIN_SAMPLES", "LOOSEN_RATE", "TIGHTEN_REVERSAL_RATE", "PASS_NAME",
    "DEFAULT_BANDS", "load_review_outcomes", "confirm_rate_by_band",
    "reversal_rate", "propose_calibration", "apply_calibration",
]
