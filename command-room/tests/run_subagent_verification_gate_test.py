#!/usr/bin/env python3
"""
Subagent numeric-verification gate (Phase 5 / R6).

A subagent's output is a delegation, not a source of truth — any number it
reports must be re-derived through a canonical code helper before it renders.
Proven necessary twice on 2026-07-01 (a −70% decay claim that was +417 events;
a "1 of 11" hand-count that was wrong).

This guard keeps the contract wired: the canonical doc exists and names the
real helpers, and every orchestrator that fans out subagents references it — so
a new number-rendering fan-out surface can't ship without the gate.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

PASS = 0
FAIL = 0


def check(label: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}")
    else:
        FAIL += 1
        print(f"  FAIL  {label}  {detail}")


DOC = ROOT / "shared" / "SUBAGENT_VERIFICATION.md"

# The canonical helpers the gate points every rendered number at.
CANONICAL_HELPERS = [
    "commitment_counts",          # commitment_state — the one counting API
    "compute_value_receipt",      # value_receipt — delivered-work counts / hours
    "event_time",                 # event_time — ts/timestamp/date field drift
    "source_ref_index",           # dedup membership
]

# Orchestrators that fan out subagents / parallel reads and render numbers.
FANOUT_ORCHESTRATORS = ["boardroom", "weekly-recap"]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("=== subagent numeric-verification gate (Phase 5 / R6) ===")

    check("canonical contract doc exists", DOC.exists(), str(DOC))
    text = DOC.read_text(encoding="utf-8") if DOC.exists() else ""

    for helper in CANONICAL_HELPERS:
        check(f"doc names canonical helper `{helper}`", helper in text, "missing from SUBAGENT_VERIFICATION.md")

    check("doc records why (the −70% / 1-of-11 failures)",
          ("-70%" in text or "−70%" in text) and "417" in text, "the proving failures must be documented")

    for skill in FANOUT_ORCHESTRATORS:
        md = ROOT / "skills" / skill / "SKILL.md"
        body = md.read_text(encoding="utf-8") if md.exists() else ""
        check(f"{skill} references the verification gate",
              "SUBAGENT_VERIFICATION.md" in body,
              f"{skill}/SKILL.md must reference shared/SUBAGENT_VERIFICATION.md")

    # RELIABILITY.md indexes it so it's discoverable in the Read-with chain.
    rel = (ROOT / "shared" / "RELIABILITY.md").read_text(encoding="utf-8")
    check("RELIABILITY.md indexes the gate", "SUBAGENT_VERIFICATION.md" in rel)

    print()
    if FAIL:
        print(f"FAIL — {FAIL} of {PASS + FAIL} checks failed")
        return 1
    print(f"OK — all {PASS} verification-gate checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
