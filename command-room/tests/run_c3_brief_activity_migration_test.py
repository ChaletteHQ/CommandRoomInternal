#!/usr/bin/env python3
"""C3 migration (RECL1 M3 follow-up, 2026-07-22) — morning-briefing's Step 3d
`thread_activity` input moves off the hand-rolled `{thread_id: max ts}` prose
scan onto the canonical `thread_activity.derive_from_events` read.

Two layers, one guard per defect class:

1. DOC-TRUTH — prose IS the executable layer (the instruction-layer-gap
   gotcha): the SKILL must instruct the canonical call (ALL_TYPES +
   honor_reclassifications=True) and must NOT instruct a hand-rolled
   max-ts scan for the thread_activity input.
2. RUNTIME — the exact instructed snippet, exercised on a fixture that pins
   all three DELIBERATE behavior deltas vs the old hand scan:
     (a) a reclassified event credits the CORRECTED thread (RECL1 fold);
     (b) a low-confidence event (< 0.40) no longer counts — an unconfirmed
         classification cannot silently mute an overdue item;
     (c) a related-thread event credits the related thread too (the fleet's
         definition of thread activity; the hand scan was primary-only);
   plus the legacy `data.primary_thread_id` spelling staying readable.

G14: all fixture timestamps relative to today. Placeholder ids only.
House convention: non-zero exit = fail.
"""
from __future__ import annotations

import datetime as dt
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

failures: list[str] = []
checks = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global checks
    checks += 1
    if not cond:
        failures.append(label)
        print(f"  FAIL {label}" + (f" — {detail}" if detail else ""))
    else:
        print(f"  ok   {label}")


def _ago(days: int) -> str:
    return (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    # ---------------------------------------------------------- doc truth
    print("-- doc truth: the SKILL instructs the canonical derivation --")
    skill = (ROOT / "skills" / "morning-briefing" / "SKILL.md").read_text(
        encoding="utf-8")
    check("SKILL instructs derive_from_events for thread_activity",
          "derive_from_events(events, activity_types=ALL_TYPES, "
          "honor_reclassifications=True)" in skill)
    check("SKILL forbids the inline scan in the same breath (C3 language)",
          "do NOT inline your own max(ts) scan" in skill)
    # The old hand-scan INSTRUCTION shape must be gone from the gather block:
    # "`{thread_id: <max ts of any event...>}` from the ... scan". The
    # dormancy check's per-PERSON max-ts (Step 3, different derivation) is
    # out of C3 scope and legitimately remains.
    check("hand-rolled thread_activity scan instruction removed",
          re.search(r"max ts of any event on that thread>?\}` from the",
                    skill) is None)

    # ------------------------------------------------------------ runtime
    print("-- runtime: the instructed snippet, three deltas pinned --")
    from thread_activity import ALL_TYPES, derive_from_events

    T_MAIN, T_OTHER, T_LEGACY = "thread_a", "thread_b", "thread_l"
    events = [
        # (c) related-thread credit: primary on MAIN, related names OTHER.
        {"seq": 1, "type": "meeting", "ts": _ago(2),
         "primary_thread_id": T_MAIN, "related_thread_ids": [T_OTHER]},
        # (b) low-confidence on OTHER, newest of all — must NOT win under
        # the floor.
        {"seq": 2, "type": "interaction", "ts": _ago(0),
         "primary_thread_id": T_OTHER, "classification_confidence": 0.2},
        # legacy data-level spelling still readable.
        {"seq": 3, "type": "note", "ts": _ago(5),
         "data": {"primary_thread_id": T_LEGACY}},
        # (a) the RECL1 fold: seq 1's envelope moves MAIN -> LEGACY. Write
        # contract (real substrate shape — the fixture gotcha): the NEW
        # envelope rides the reclassification event's own TOP LEVEL; the
        # data.old/new_* fields are the audit record, not the patch source.
        {"seq": 4, "type": "reclassification", "ts": _ago(1),
         "supersedes_seq": 1,
         "primary_thread_id": T_LEGACY,
         "related_thread_ids": [T_OTHER],
         "classification_confidence": 1.0,
         "data": {"old_primary_thread_id": T_MAIN,
                  "new_primary_thread_id": T_LEGACY,
                  "old_related_thread_ids": [T_OTHER],
                  "new_related_thread_ids": [T_OTHER],
                  "reason": "test fixture"}},
    ]
    ta = {tid: act.ts.isoformat()
          for tid, act in derive_from_events(
              events, activity_types=ALL_TYPES,
              honor_reclassifications=True).items()}

    # Kept-in-stream semantic (RECL1 A6 pin — ruled, don't fight it): under
    # ALL_TYPES the reclassification event ITSELF is activity, so the
    # corrected thread's "last touched" is CORRECTION time (_ago(1)), not
    # the moved event's own ts (_ago(2)). What matters for the brief's
    # 7-day stopgap: the credit is on the CORRECTED thread, in-window.
    check("(a) corrected thread carries the credit (correction-time ts)",
          T_LEGACY in ta and ta[T_LEGACY].startswith(_ago(1)[:10]),
          str(ta))
    check("(a) the old thread no longer carries seq 1's credit",
          T_MAIN not in ta or not ta[T_MAIN].startswith(_ago(2)[:10]),
          str(ta.get(T_MAIN)))
    check("(b) low-confidence event never counts (0.2 < 0.40 floor) — "
          "OTHER's latest is the reclass related-credit, not seq 2",
          ta.get(T_OTHER, "").startswith(_ago(1)[:10]),
          str(ta.get(T_OTHER)))
    check("(c) related thread credited by seq 1 (fleet definition)",
          T_OTHER in ta, str(sorted(ta)))
    # Contrast pin: WITHOUT the fold, the raw read still credits T_MAIN —
    # proving honor_reclassifications is what moves the credit (a mutation
    # dropping the kwarg from the SKILL snippet flips check (a)).
    raw = derive_from_events(events, activity_types=ALL_TYPES)
    check("contrast: raw read credits the OLD thread (fold is load-bearing)",
          T_MAIN in raw, str(sorted(raw)))

    print()
    if failures:
        print(f"{len(failures)} FAILED of {checks}")
        return 1
    print(f"OK — all {checks} C3 brief-activity migration checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
