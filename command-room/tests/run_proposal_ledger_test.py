#!/usr/bin/env python3
"""Phase 6 — shared proposal ledger: 60-day cooldown + global proposal cap."""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import proposal_ledger as pl  # noqa: E402

_failures = []


def check(name, cond):
    print(f"{'OK  ' if cond else 'FAIL'} {name}")
    if not cond:
        _failures.append(name)


def _iso(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def test_append_and_load():
    ws = Path(tempfile.mkdtemp(prefix="pl_"))
    check("append returns True",
          pl.append_decision(ws, pass_name="pass13_sender_priority",
                             fingerprint="fp_a", user_action="declined", summary="s"))
    rows = pl.load_rows(ws)
    check("one row loaded", len(rows) == 1 and rows[0]["fingerprint"] == "fp_a")
    check("pass filter works",
          pl.load_rows(ws, pass_name="other") == [])


def test_cooldown_window():
    rows = [
        {"pass": "p13", "fingerprint": "recent", "user_action": "declined", "ts": _iso(10)},
        {"pass": "p13", "fingerprint": "old", "user_action": "declined", "ts": _iso(70)},
        {"pass": "p13", "fingerprint": "applied_fp", "user_action": "applied", "ts": _iso(5)},
        {"pass": "other", "fingerprint": "wrongpass", "user_action": "declined", "ts": _iso(1)},
    ]
    cd = pl.active_cooldowns(None, "p13", now_iso=_iso(0), rows=rows)
    check("recent decline in cooldown", "recent" in cd)
    check("70-day decline expired", "old" not in cd)
    check("applied is NOT a cooldown", "applied_fp" not in cd)
    check("other pass excluded", "wrongpass" not in cd)


def test_malformed_ts_fails_safe():
    rows = [{"pass": "p", "fingerprint": "bad", "user_action": "declined", "ts": "notadate"}]
    cd = pl.active_cooldowns(None, "p", now_iso=_iso(0), rows=rows)
    check("unparseable ts treated as in-cooldown (no re-nag)", "bad" in cd)


def test_global_cap():
    check("global cap constant is 7", pl.GLOBAL_PROPOSAL_CAP == 7)
    check("remaining after 4 rendered → 3", pl.remaining_global_slots(4) == 3)
    check("remaining never negative", pl.remaining_global_slots(99) == 0)
    check("remaining custom cap", pl.remaining_global_slots(1, cap=3) == 2)


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    test_append_and_load()
    test_cooldown_window()
    test_malformed_ts_fails_safe()
    test_global_cap()
    print()
    if _failures:
        print(f"{len(_failures)} FAILED: " + ", ".join(_failures))
        return 1
    print("ALL proposal_ledger tests PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
