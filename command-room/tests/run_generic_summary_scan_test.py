#!/usr/bin/env python3
"""
Tests for `scan_for_generic_summary` (SPEC EXEC1 element 1 — the anti-washing
floor). Catches the four canonical generic-summary washing shapes on exec-header
lines, and NOTHING MORE — concrete header lines (named entity / number / date)
and the explicit nothing-forms pass clean.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared", "scripts"))

from chat_output_renderer import scan_for_generic_summary

results = {"pass": 0, "fail": 0, "failures": []}


def check(name, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  PASS  {name}")
    else:
        results["fail"] += 1
        results["failures"].append(f"{name} ({detail})" if detail else name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


# ---- the four banned shapes are caught ----
print("\n=== banned generic-summary shapes ===")
for phrase in [
    "Several important updates this week.",
    "Several updates since Tuesday.",
    "Key developments across the portfolio.",
    "Key development on the Acme front.",
    "Busy week across the board.",
    "Lots of movement this period.",
]:
    check(f"flags {phrase!r}", len(scan_for_generic_summary(phrase)) >= 1, "expected a finding")

# ---- concrete header lines pass ----
print("\n=== concrete header lines pass ===")
for phrase in [
    "Acme moved from handshake to paperwork; Northstar went 13 days quiet.",
    "Whether to gate the sales hire on the playbook — by Jun 15 (board).",
    "Approve the redline (one tap below) · nothing else.",
    "Ratify the Acme renewal by Friday.",
]:
    check(f"clean: {phrase!r}", scan_for_generic_summary(phrase) == [], "expected no finding")

# ---- the explicit nothing-forms pass ----
print("\n=== nothing-forms pass ===")
for phrase in [
    "Nothing material since Tuesday's brief.",
    "Nothing — execution day.",
    "Nothing from you.",
]:
    check(f"nothing-form clean: {phrase!r}", scan_for_generic_summary(phrase) == [])

# ---- empty / None safe ----
print("\n=== edge cases ===")
check("empty string → []", scan_for_generic_summary("") == [])
check("None → []", scan_for_generic_summary(None) == [])


print(f"\n=== {results['pass']} passed, {results['fail']} failed ===")
if results["fail"]:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
