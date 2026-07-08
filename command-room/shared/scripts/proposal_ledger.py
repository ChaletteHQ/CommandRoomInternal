#!/usr/bin/env python3
"""
Proposal ledger — shared cooldown + decision log for the Phase 6 learning-loop
review passes (insight-generator Passes 13/14/15, plus the Loop 4/6 calibration
steps).

WHY A SEPARATE FILE FROM classifier_feedback.jsonl
--------------------------------------------------
Passes 8-10 log their decisions to `_hq/data/classifier_feedback.jsonl`, whose
rows MUST validate against `shared/data-schemas/classifier_feedback.schema.json`
(ts + event_seq + user_action + confidence_before — a classification-row shape).
The learning-loop proposals have no `event_seq`/`confidence_before`; forcing them
into that file would either break the schema contract or dilute it. So the new
passes get a dedicated, same-pattern ledger — append-only JSONL, one row per
user decision — that carries exactly the fields the cooldown machinery needs.
This is the "classifier_feedback.jsonl-style log" the Phase 6 spec calls for,
reused verbatim in shape (fingerprint + user_action + ts) without coupling to a
schema built for a different row.

CONTRACT (mirrors Pass 9/10 cooldown semantics exactly)
-------------------------------------------------------
  - Each proposing pass writes ONE row per user decision:
    {ts, pass, fingerprint, user_action, summary}
    user_action in {"applied", "edited", "declined", "skipped"}.
  - A `declined` fingerprint enters a 60-day cooldown (measured from the decline
    ts) — the pass suppresses that fingerprint at scoring time until it expires.
    `skipped` is a soft defer (re-surfaces next run, no cooldown); `applied` /
    `edited` mean the rule landed in its override store.
  - `pass` namespaces the ledger so one file serves every loop without
    fingerprint collisions across passes.

GLOBAL PROPOSAL CAP
-------------------
Each pass keeps its own 3-cap (Pass 9 precedent). On top of that, the weekly
review honors ONE global cap across all proposing passes so a busy week doesn't
bury the CEO under a dozen prompts at once (Phase 6 spec: "four new proposing
passes ride one weekly widget"). `GLOBAL_PROPOSAL_CAP` is that ceiling; passes
render in priority order and `remaining_global_slots()` tells a later pass how
many slots are left after the earlier passes claimed theirs.

All writes land under `_hq/data/` in the customer workspace — NEVER the plugin
directory. stdlib only; never raises into a caller.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Optional, Set

try:
    from event_time import event_time
except Exception:  # pragma: no cover
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from event_time import event_time  # type: ignore

# The weekly review renders at most this many interactive proposals TOTAL across
# every proposing pass (13/14/15 + Loop 4/6), on top of each pass's own 3-cap.
GLOBAL_PROPOSAL_CAP = 7

COOLDOWN_DAYS = 60

_APPLIED = {"applied", "edited"}
_DECLINE = "declined"


def ledger_path(workspace_root) -> Path:
    return Path(workspace_root) / "_hq" / "data" / "proposal_feedback.jsonl"


def _now_iso() -> str:
    # Kept out of the pure helpers; only the append path stamps a clock.
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_rows(workspace_root, pass_name: Optional[str] = None) -> List[dict]:
    """All ledger rows (optionally filtered to one pass). Tolerant of a missing
    file and malformed lines; never raises."""
    path = ledger_path(workspace_root)
    if not path.exists():
        return []
    out: List[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if pass_name is not None and row.get("pass") != pass_name:
            continue
        out.append(row)
    return out


def append_decision(
    workspace_root,
    *,
    pass_name: str,
    fingerprint: str,
    user_action: str,
    summary: str = "",
) -> bool:
    """Append one decision row. Returns True on write, False on failure. Uses the
    canonical atomic append (never a hand-rolled write). Never raises."""
    try:
        from atomic_write import atomic_append_jsonl
    except Exception:  # pragma: no cover
        import sys as _sys
        _sys.path.insert(0, str(Path(__file__).resolve().parent))
        try:
            from atomic_write import atomic_append_jsonl  # type: ignore
        except Exception:
            return False
    row = {
        "ts": _now_iso(),
        "pass": pass_name,
        "fingerprint": fingerprint,
        "user_action": user_action,
        "summary": summary[:300] if summary else "",
    }
    try:
        path = ledger_path(workspace_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_append_jsonl(path, [row])
        return True
    except Exception:
        return False


def _days_between(a_iso: Optional[str], b_iso: Optional[str]) -> Optional[float]:
    from datetime import datetime

    def _parse(s):
        if not s:
            return None
        try:
            return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        except Exception:
            return None

    da, db = _parse(a_iso), _parse(b_iso)
    if da is None or db is None:
        return None
    return (db - da).total_seconds() / 86400.0


def active_cooldowns(
    workspace_root,
    pass_name: str,
    *,
    now_iso: str,
    cooldown_days: int = COOLDOWN_DAYS,
    rows: Optional[Iterable[dict]] = None,
) -> Set[str]:
    """Fingerprints in `pass_name` that were DECLINED within the last
    `cooldown_days` — the pass drops these from candidates at scoring time
    (Pass 9/10 60-day cooldown, verbatim). `applied`/`edited` are NOT cooldowns
    (the rule already lives in its store); a later re-decline restarts the clock.
    Pass `rows` to score against an in-memory ledger (tests); otherwise loaded
    from disk."""
    if rows is None:
        rows = load_rows(workspace_root, pass_name)
    out: Set[str] = set()
    for row in rows:
        if row.get("pass") != pass_name:
            continue
        if row.get("user_action") != _DECLINE:
            continue
        fp = row.get("fingerprint")
        if not fp:
            continue
        age = _days_between(row.get("ts"), now_iso)
        if age is None or age < cooldown_days:
            # Unknown age (malformed ts) is treated as in-cooldown — fail safe
            # toward NOT re-nagging.
            out.add(fp)
    return out


def remaining_global_slots(rendered_so_far: int, cap: int = GLOBAL_PROPOSAL_CAP) -> int:
    """Slots left under the weekly global cap after earlier passes rendered
    `rendered_so_far` proposals. Never negative."""
    return max(0, cap - max(0, int(rendered_so_far)))


__all__ = [
    "GLOBAL_PROPOSAL_CAP",
    "COOLDOWN_DAYS",
    "ledger_path",
    "load_rows",
    "append_decision",
    "active_cooldowns",
    "remaining_global_slots",
]
