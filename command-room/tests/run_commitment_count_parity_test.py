#!/usr/bin/env python3
"""Guard for the v3.18.3 coach-vs-brief commitment-count parity (Bug #85 layer 2).

The v3.18.1 test pass found the coach reporting 4 open commitments while the
morning brief reported ~18 for the same substrate. Both skills already SPEC the
same canonical reader (`cru_match.load_open_commitments`) — the divergence was
the coach freelancing an aggressive post-filter on the headline number.

This test pins the parity invariant in code:
  canonical total = len(load_open_commitments(events))
                  = compute_brief_state(...).counts.you_owe + counts.they_owe
so the coach's `open_commitments` headline (which MUST equal the canonical
total) and the brief's header total are the same number by construction.

Plus a source-side guard: the coach SKILL.md carries the hard count gate, and
the brief SKILL.md computes its header via compute_brief_state.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from cru_match import load_open_commitments, _commitment_id  # noqa: E402
from brief_state import compute_brief_state  # noqa: E402

USER = "person_user"

passed = 0
failed = 0


def check(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def commitment(seq, owner, title, due=None):
    # owner=None → ownerless (null owner_id), the A85 case.
    data = {"id": f"c{seq}", "title": title, "status": "open"}
    if owner is not None:
        data["owner_id"] = owner
    if due:
        data["due"] = due
    return {"seq": seq, "type": "commitment", "primary_thread_id": f"t{seq}", "data": data}


def main():
    print("=== coach-vs-brief commitment-count parity (Bug #85 + A85 followup) ===\n")

    # Mixed open set: 3 user-owned, 2 counterparty-owned, AND 2 OWNERLESS — the
    # exact A85 shape (the v3.18.4 brief reported 16 = you+they while the coach
    # reported 18 = len, diverging by the 2 ownerless items). The earlier fixture
    # had no ownerless rows, so it false-greened while real data failed.
    import json, tempfile
    opens_events = [
        commitment(1, USER, "Send Bob the deck"),
        commitment(2, USER, "Draft the SOW", due="2020-01-01"),  # overdue → stuck, still counts
        commitment(3, USER, "Reply to the investor update"),
        commitment(4, "person_bob", "Bob returns the signed contract"),
        commitment(5, "person_amy", "Amy sends the vendor quote"),
        commitment(6, None, "Bob to provide a BAA"),          # ownerless (A85)
        commitment(7, None, "Jake to download Granola"),      # ownerless (A85)
    ]
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ev in opens_events:
            f.write(json.dumps(ev) + "\n")

    opens = load_open_commitments(path)
    canonical_total = len(opens)  # 7

    brief = compute_brief_state(
        open_commitments=opens,
        user_person_id=USER,
        now_iso="2026-05-31T08:00:00",
    )
    c = brief["counts"]
    os.unlink(path)

    print("[1] canonical total == brief counts.total == len(load_open_commitments)")
    check(
        "counts.total == len(open) == 7",
        c.get("total") == canonical_total == 7,
        f"counts.total={c.get('total')} len={canonical_total} (you={c['you_owe']} they={c['they_owe']} unowned={c.get('unowned')})",
    )
    check(
        "total == you_owe + they_owe + unowned (ownerless accounted for, not dropped)",
        c.get("total") == c["you_owe"] + c["they_owe"] + c.get("unowned", 0),
        f"{c}",
    )
    check(
        "the 2 ownerless commitments are counted as unowned (not silently dropped)",
        c.get("unowned") == 2,
        f"unowned={c.get('unowned')} — expected 2",
    )

    print("\n[2] the coach headline (len) agrees with the brief total — the A85 fix")
    coach_open_commitments = len(opens)  # coach gate: len(load_open_commitments)
    check(
        "coach len == brief counts.total",
        coach_open_commitments == c.get("total"),
        f"coach={coach_open_commitments} brief.total={c.get('total')} — MUST match (Bug #85)",
    )
    # Regression anchor: the OLD (buggy) invariant you_owe+they_owe would be 5,
    # diverging from len=7 — exactly the 16-vs-18 A85 failure. Assert the fix
    # closed that gap.
    check(
        "the old you+they-only total would have UNDERCOUNTED (proves A85 is fixed)",
        (c["you_owe"] + c["they_owe"]) == 5 and coach_open_commitments == 7,
        f"you+they={c['you_owe'] + c['they_owe']} vs len={coach_open_commitments}",
    )

    print("\n[3] the stuck subset is a call-out, NOT a shrink of the headline")
    check(
        "stuck (overdue) is counted but does not reduce the total",
        c["stuck"] == 1 and c.get("total") == 7,
        f"stuck={c['stuck']} total={c.get('total')}",
    )

    # --- source-side gates ---
    print("\n[4] coach SKILL.md carries the hard count gate")
    coach = open(os.path.join(PLUGIN_ROOT, "skills", "command-room-coach", "SKILL.md"), encoding="utf-8").read()
    check(
        "coach gate names load_open_commitments + counts.total + Bug #85",
        "load_open_commitments" in coach and "counts.total" in coach and "Bug #85" in coach,
        "coach must equate its count to the brief's counts.total (= you+they+unowned), not you+they",
    )
    check(
        "coach gate forbids shrinking the headline to a subset",
        "Do NOT post-filter the headline" in coach or "never by shrinking the headline" in coach,
        "coach must forbid the aggressive headline filter that caused the 4-vs-18 split",
    )

    print("\n[5] morning-briefing computes the header via compute_brief_state")
    brief_md = open(os.path.join(PLUGIN_ROOT, "skills", "morning-briefing", "SKILL.md"), encoding="utf-8").read()
    check(
        "brief references compute_brief_state for the header counts",
        "compute_brief_state" in brief_md,
        "the brief header total must come from compute_brief_state (the shared computation)",
    )
    check(
        "brief uses compute_and_log_brief_state so a bypass is DETECTABLE (Bug #99)",
        "compute_and_log_brief_state" in brief_md and "brief_state" in brief_md,
        "the brief hand-rolled counts instead of calling compute_brief_state (matched by luck); "
        "the logging wrapper emits a brief_state event with the code's real numbers so a "
        "hand-rolled brief (no brief_state event) is detectable, per the #98-v3 lesson",
    )
    check(
        "brief reads the reconcile-sent task's closures (Bug #98-v3 — reconciliation moved to its own task)",
        "reconcile-sent" in brief_md and "3a-bis" in brief_md and "sent_reconcile" in brief_md,
        "the brief no longer RUNS reconciliation (it was skipped 3x as a brief sub-step); it READS "
        "the sent_reconcile/commitment_resolved events the dedicated reconcile-sent task wrote",
    )
    check(
        "brief is a READER with the deterministic reconcile_stale floor; reconciliation lives in the dedicated task (Bug #98-v3)",
        "reconcile_and_receipt" not in brief_md
        and "reconcile_stale" in brief_md
        and "sent_reconcile" in brief_md,
        "v3.18.12 moved reconciliation out of the brief into the silent reconcile-sent task "
        "(three folds were skipped — invisible write loses to visible deliverable). The brief now "
        "READS the sent_reconcile/commitment_resolved events the task wrote and keeps the deterministic "
        "compute_brief_state reconcile_stale soften floor so it never surfaces redo-work.",
    )

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
