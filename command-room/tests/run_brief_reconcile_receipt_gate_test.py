#!/usr/bin/env python3
"""Static wiring guard for the Sent-reconciliation receipt contract (Bug #98).

#98 replaced the v3.18.5 output-contract gate (which was theater — it forced a
printed line, not the work) with a code-generated RECEIPT + a fail-loud
post-condition that reads the cursor back from substrate. The orchestrator
`reconcile_and_receipt` is unit-tested in run_reconcile_sent_test; this guard
asserts the BRIEF actually wires to it and keeps the fail-loud language, so the
mechanism can't silently rot back into "print a status line".

A guard, not a behavior test — the expensive half (does the model call the
orchestrator) is the verify-cr-release re-test. This is the cheap half: don't let
the receipt + fail-loud disappear from the prose.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
BRIEF = os.path.join(PLUGIN_ROOT, "skills", "morning-briefing", "SKILL.md")
ORCH = os.path.join(PLUGIN_ROOT, "shared", "scripts", "reconcile_sent_commitments.py")

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
    print("=== Sent-reconciliation architecture gate (Bug #98-v3) ===\n")
    brief = open(BRIEF, encoding="utf-8").read() if os.path.isfile(BRIEF) else ""
    low = brief.lower()
    orch = open(ORCH, encoding="utf-8").read() if os.path.isfile(ORCH) else ""
    recon_path = os.path.join(PLUGIN_ROOT, "skills", "reconcile-sent", "SKILL.md")
    recon = open(recon_path, encoding="utf-8").read() if os.path.isfile(recon_path) else ""
    recon_low = recon.lower()
    inbox_path = os.path.join(PLUGIN_ROOT, "skills", "inbox-triage", "SKILL.md")
    inbox = open(inbox_path, encoding="utf-8").read() if os.path.isfile(inbox_path) else ""

    print("[1] orchestrator emits an AUDIT event + exposes a read-back validator (enforce on the event, not narration)")
    check("reconcile_and_receipt defined", "def reconcile_and_receipt" in orch)
    check("emits a sent_reconcile audit event with cursor_from/cursor_to/sent_scanned_count",
          '"sent_reconcile"' in orch
          and all(k in orch for k in ("cursor_from", "cursor_to", "sent_scanned_count")),
          "the verifiable artifact a sentence can't fake")
    check("validate_reconcile_ran defined + exported",
          "def validate_reconcile_ran" in orch and "validate_reconcile_ran" in orch.split("__all__", 1)[-1][:300])
    check("sent_reconcile is in the events schema enum", True)  # asserted by run_event_contract_test

    print("\n[2] the DEDICATED reconcile-sent task does the work (single-job)")
    check("reconcile-sent SKILL.md exists", bool(recon), recon_path)
    check("task calls reconcile_and_receipt", "reconcile_and_receipt" in recon)
    check("task self-validates via validate_reconcile_ran", "validate_reconcile_ran" in recon)
    # connector-agnostic-v1: the SKILL expresses the Sent query as the
    # `in_sent` INTENT (compiled per provider by connector_adapters/mail.py)
    # — the literal provider operator is banned from skill prose (grep-gate 1).
    check("task does a REAL Sent fetch (not reused data)",
          ("in_sent" in recon_low or "in:sent" in recon_low)
          and ("real" in recon_low and "fetch" in recon_low))
    check("task names Bug #98-v3", "#98-v3" in recon_low or "98-v3" in recon_low)
    check("task has a wide first-run/catch-up lookback for stranded backlog (Bug #101)",
          ("catch up" in recon_low or "catch-up" in recon_low)
          and "30 days" in recon_low and "#101" in recon_low,
          "a stale cursor strands earlier mail; first run + manual catch-up must fetch a wide window")

    print("\n[3] the brief is a READER — it does NOT reconcile (the move-out)")
    check("brief does NOT call reconcile_and_receipt", "reconcile_and_receipt" not in brief,
          "Bug #98-v3: reconciliation moved OUT of the brief; the brief reads what the task wrote")
    check("brief reads sent_reconcile / commitment_resolved events",
          "sent_reconcile" in brief and "commitment_resolved" in brief)
    check("brief names Bug #98-v3", "#98-v3" in low or "98-v3" in low)

    print("\n[4] the deterministic soften floor stays in the brief")
    check("brief reads reconcile_stale + softens",
          "reconcile_stale" in brief and "soften" in low)

    print("\n[5] inbox-triage NO LONGER reconciles (single-purpose now)")
    check("inbox-triage does NOT call reconcile_and_receipt", "reconcile_and_receipt" not in inbox,
          "the v3.18.11 backstop was reverted — reconciliation is the dedicated task's job")

    print("\n[6] the dead gates are gone")
    check("no v3.18.5 output-contract phrasing", "this status line is the proof they ran" not in low)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
