#!/usr/bin/env python3
"""Static wiring guard for the morning brief's "ball is on you" gate (Bug #93).

#93 is an LLM-runtime-behavior fix — its real proof is the Cowork verify loop —
but the *gate language* it depends on must not silently disappear from the
SKILL.md (the way the v3.18.5 reconciliation line was present yet skipped). This
guard asserts the four structural pieces of the fix are still in the prose:

  (core/a) a single gated source — Step 3e — that every "ball is on you"
           actionable, INCLUDING Top 3 moves, must flow through;
  (b)      Step 3c-bis issues its OWN wide calendar fetch and must not reuse the
           narrow today/tomorrow display pull;
  (c)      Step 3c fails CLOSED on a get_thread error instead of inferring the
           latest sender from a search snippet;
  (link)   the Top-3-moves layout block points back at the gate.

A guard, not a behavior test: it catches a regression that deletes/guts the gate,
which is the cheap half of keeping #93 fixed. The expensive half (does the model
obey it) is the verify-cr-release re-test.
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
BRIEF = os.path.join(PLUGIN_ROOT, "skills", "morning-briefing", "SKILL.md")

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


def main():
    print("=== morning brief ball-on-you gate (Bug #93) ===\n")
    body = open(BRIEF, encoding="utf-8").read() if os.path.isfile(BRIEF) else ""
    low = body.lower()

    print("[core/a] single gated source for every ball-on-you actionable")
    check("Step 3e exists", "step 3e" in low, "Step 3e heading missing")
    check("names Bug #93", "bug #93" in low, "#93 marker missing")
    check("routes Top 3 moves through the gate (not a separate synthesis)",
          "top 3 moves" in low and "state[\"needs_attention\"]" in body,
          "must tie Top 3 moves to state['needs_attention']")
    check("forbids promoting a dropped item",
          ("dropped" in low and "never promote" in low) or "may not be promoted" in low
          or "never promote an item compute_brief_state dropped" in low,
          "must forbid resurrecting a compute_brief_state-dropped item")

    print("\n[b] Step 3c-bis uses its own WIDE calendar fetch, not the narrow pull")
    check("Step 3c-bis present", "3c-bis" in low)
    check("dedicated/wide fetch language present",
          ("dedicated wide fetch" in low or "wide fetch" in low
           or "do not reuse step 2" in low or "30 days" in low),
          "must mandate a wide list_events window")
    check("explicitly warns against reusing the narrow display pull",
          "narrow" in low and "display" in low,
          "must contrast against the today/tomorrow display pull")

    print("\n[c] Step 3c fails CLOSED on a get_thread error (no snippet inference)")
    check("fail-closed language present",
          "fail closed" in low or "fail-closed" in low or "fail closed on any" in low,
          "Step 3c must fail closed on get_thread error")
    check("forbids snippet inference",
          "snippet" in low and ("never infer" in low or "not infer" in low or "do not infer" in low),
          "must forbid inferring latest-sender from a search snippet")
    check("calls out message-id vs thread-id",
          ("message-id" in low or "message id" in low) and ("thread-id" in low or "threadid" in low),
          "must warn that get_thread wants a thread-id, not a message-id")

    print("\n[link] the Top-3-moves layout block references the gate")
    # The layout block carries an inline GATE pointer tagged with #93.
    check("Top-3-moves layout carries a #93 GATE pointer",
          bool(re.search(r"gate \(bug #93\)", low)),
          "Top 3 moves layout must carry an inline GATE (Bug #93) pointer")

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
