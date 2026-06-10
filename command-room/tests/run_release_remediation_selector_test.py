#!/usr/bin/env python3
"""Tests for release_remediation_selector — deterministic manifest selection for
the update-bridge (v3.18.9+ "solid for all clients" hardening).

The whole point is that version selection must NOT be a string sort/compare. The
load-bearing test is the 3.9.1 -> 3.18.9 upgrade: a client on an old single-digit
minor must still receive every 3.10–3.18 manifest. A string filter (`v > "3.9.1"`)
drops them all because "3.10.0" < "3.9.1" lexically. These tests run against the
REAL shipped manifests so they can't drift from production reality, plus a
synthetic double-digit case for the future-proofing claim.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import release_remediation_selector as sel  # noqa: E402

REAL_RELEASES = os.path.join(PLUGIN_ROOT, "shared", "releases")

passed = 0
failed = 0


def check(name, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {name}")
    else:
        failed += 1
        print(f"  FAIL {name}")
        if detail:
            print(f"      {detail}")


def versions(result):
    return [r["version"] for r in result]


def main():
    print("=== release_remediation_selector (solid for all clients) ===\n")

    print("[1] parse_version + tuple ordering")
    check("parse '3.13.8.1'", sel.parse_version("3.13.8.1") == (3, 13, 8, 1))
    check("3.9.1 < 3.10.0 (the string-sort trap)",
          sel.parse_version("3.9.1") < sel.parse_version("3.10.0"))
    check("3.13.8 < 3.13.8.1 (4-part patch sorts after its base)",
          sel.parse_version("3.13.8") < sel.parse_version("3.13.8.1"))
    check("3.14.2 < 3.14.10 (double-digit patch)",
          sel.parse_version("3.14.2") < sel.parse_version("3.14.10"))

    print("\n[1b] version_lt / version_ge — the only_if-gate boundaries")
    check("2.9.0 < 2.10.3 (string compare gets this WRONG)", sel.version_lt("2.9.0", "2.10.3"))
    check("2.14.2 < 2.14.12 (string compare gets this WRONG)", sel.version_lt("2.14.2", "2.14.12"))
    check("3.14.4 NOT < 2.10.3 (current client past the gate)", not sel.version_lt("3.14.4", "2.10.3"))
    check("2.10.3 NOT < 2.10.3 (boundary is exclusive)", not sel.version_lt("2.10.3", "2.10.3"))
    check("version_ge mirror: 2.10.3 >= 2.10.3", sel.version_ge("2.10.3", "2.10.3"))
    check("version_ge: 2.9.0 NOT >= 2.10.3", not sel.version_ge("2.9.0", "2.10.3"))

    print("\n[2] THE load-bearing case: 3.9.1 -> 3.18.9 keeps every 3.10–3.18 manifest")
    res = sel.select_pending_manifests(REAL_RELEASES, "3.9.1", "3.18.9")
    vs = versions(res)
    # A naive STRING filter would exclude these (because "3.10.0" < "3.9.1").
    must_include = ["3.10.0", "3.11.0", "3.12.0", "3.13.0", "3.14.0", "3.15.0", "3.18.6", "3.18.9"]
    for v in must_include:
        check(f"includes {v}", v in vs, vs)
    check("3.9.1 itself is EXCLUDED (last_applied is exclusive)", "3.9.1" not in vs)
    check("nothing <= 3.9.1 leaks in (e.g. 3.9.0, 3.8.0)",
          "3.9.0" not in vs and "3.8.0" not in vs, vs)
    # Prove we beat the string filter: how many would a lexical filter wrongly drop?
    all_v = [os.path.basename(p)[1:-5] for p in
             __import__("glob").glob(os.path.join(REAL_RELEASES, "v*.json"))]
    string_filtered = sorted(v for v in all_v if v > "3.9.1" and v <= "3.18.9")
    dropped_by_string = sorted(set(vs) - set(string_filtered), key=sel.parse_version)
    check("selector recovers manifests a string filter would silently drop",
          len(dropped_by_string) > 0,
          f"string filter would miss: {dropped_by_string}")

    print("\n[3] ascending order")
    check("result is sorted ascending by version",
          [sel.parse_version(v) for v in vs] == sorted(sel.parse_version(v) for v in vs))

    print("\n[4] current is INCLUSIVE, last_applied is EXCLUSIVE")
    res2 = sel.select_pending_manifests(REAL_RELEASES, "3.14.4", "3.18.9")
    vs2 = versions(res2)
    check("3.18.9 included (current is inclusive)", "3.18.9" in vs2)
    check("3.14.4 excluded (last_applied is exclusive)", "3.14.4" not in vs2)
    check("3.14.3 and below excluded", "3.14.3" not in vs2 and "3.13.8" not in vs2, vs2)
    # In THIS range the only manifest with items is 3.18.6 — make sure it's selected.
    check("the only items-bearing manifest in 3.14.4->3.18.9 (v3.18.6) is selected",
          "3.18.6" in vs2 and any(r["version"] == "3.18.6" and r["n_items"] >= 1 for r in res2), res2)

    print("\n[5] legacy / boundary inputs")
    # Use a ceiling above every shipped version so "every manifest plays" stays
    # true as new releases are added (don't hardcode the current top version).
    res_all = sel.select_pending_manifests(REAL_RELEASES, "0.0.0", "999.0.0")
    check("0.0.0 legacy install -> every manifest plays",
          len(res_all) == len(all_v), f"{len(res_all)} vs {len(all_v)}")
    res_none = sel.select_pending_manifests(REAL_RELEASES, "999.0.0", "999.0.0")
    check("last_applied == current -> nothing to apply", res_none == [])
    res_future = sel.select_pending_manifests(REAL_RELEASES, "999.0.0", "999.9.0")
    check("already past everything shipped -> empty", res_future == [])

    print("\n[6] synthetic double-digit dir — future-proofing")
    tmp = tempfile.mkdtemp(prefix="cr-relsel-")
    for v in ("3.14.2", "3.14.7", "3.14.10", "3.18.9", "junk", "v-bad"):
        # write valid-looking manifests; 'junk'/'v-bad' are non-version filenames
        fn = f"v{v}.json" if v not in ("junk", "v-bad") else f"{v}.json"
        with open(os.path.join(tmp, fn), "w", encoding="utf-8") as f:
            json.dump({"version": v, "headline": "", "items": []}, f)
    r = versions(sel.select_pending_manifests(tmp, "3.14.2", "3.18.9"))
    check("double-digit ordered correctly (3.14.7 before 3.14.10)",
          r == ["3.14.7", "3.14.10", "3.18.9"], r)
    check("3.14.2 excluded (exclusive low bound) even as a string-prefix",
          "3.14.2" not in r)
    check("non-version filenames ignored (no crash, not selected)",
          "junk" not in r and "v-bad" not in r, r)
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n[7] wiring — the update-bridge CALLS the selector, no hand-rolled version math")
    bridge_path = os.path.join(PLUGIN_ROOT, "skills", "command-room-update-bridge", "SKILL.md")
    bridge = open(bridge_path, encoding="utf-8").read() if os.path.isfile(bridge_path) else ""
    check("bridge invokes release_remediation_selector.py",
          "release_remediation_selector.py" in bridge,
          "Phase 4.8a must shell into the deterministic selector")
    check("bridge explicitly warns against hand string-comparing versions",
          "do NOT compare versions by hand" in bridge or "NOT by string-filtering" in bridge,
          "the gate must forbid LLM-prose version math")
    check("bridge still documents the >last_applied AND <=current contract",
          "last_applied" in bridge and "current" in bridge)
    check("bridge only_if gates use version_lt (numeric), not string compare",
          "version_lt" in bridge,
          "the only_if from_version gates must compare numerically too")

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
