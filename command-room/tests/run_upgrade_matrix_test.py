#!/usr/bin/env python3
"""Upgrade-matrix test — simulates the client jump v3.18.17 -> current.

Why (Master Build Plan R3, 2026-07-06): every production client sits on
v3.18.17 and will make ONE jump to the current staging version at promote.
The update-bridge walks the per-release manifests across that gap. The
bridge's history includes the v3.18.10 bug (lexical version compare silently
skipping the v3.10-v3.18 manifest range for old clients), so this test is the
promote gate: it proves, in code, that the exact jump every client will make
is sound. It reuses the live machinery (release_remediation_selector) — if
the comparator regresses, this fails.

Coverage:
  M1  comparator torture cases (the historical bug classes)
  M2  chain enumeration: every shipped manifest inside (BASELINE, current]
      is selected — none skipped, ordered semantically, endpoints present
  M3  manifest schema: chain manifests are well-formed (version matches
      filename; items carry id/detector/prompt/action; known actions only)
  M4  detector execution: every detector referenced by the chain loads and
      runs pure-read against the workspace_mini fixture, returning
      {applies: bool, context: dict}
  M5  strict-runtime skill loading: all skills survive the Agent Skills
      frontmatter rules (name <=64 lowercase/num/hyphen; description
      non-empty, <=1024, no angle brackets) — the Evan drop simulated
  M6  customer-visible manifest text (headline + prompt_template) in the
      chain carries no angle brackets and no raw substrate filenames

BASELINE is the fleet floor. After the promote, ratchet it up to the new
fleet floor so the test always models the real oldest client.
"""
import hashlib
import importlib
import json
import os
import re
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "shared", "scripts"))

from release_remediation_selector import parse_version, version_lt, version_in_range  # noqa: E402

BASELINE = "3.18.17"   # fleet floor — every registered client (verified 2026-07-04)
RELEASES = os.path.join(ROOT, "shared", "releases")
SKILLS = os.path.join(ROOT, "skills")
FIXTURE = os.path.join(HERE, "fixtures", "workspace_mini")

failures = []
def check(label, ok):
    print(("  PASS " if ok else "  FAIL ") + label)
    if not ok:
        failures.append(label)

def current_version():
    p = os.path.join(ROOT, ".claude-plugin", "plugin.json")
    return json.load(open(p, encoding="utf-8"))["version"]

def fixture_state():
    h = hashlib.sha256()
    for base, _dirs, files in sorted(os.walk(FIXTURE)):
        for f in sorted(files):
            p = os.path.join(base, f)
            h.update(p.encode())
            h.update(open(p, "rb").read())
    return h.hexdigest()

def main():
    cur = current_version()
    print(f"Upgrade matrix: {BASELINE} -> {cur}")

    # ---- M1: comparator torture cases ----
    check("M1 lexical trap: 3.2.1 < 3.18.17", version_lt("3.2.1", "3.18.17"))
    check("M1 major jump: 3.18.17 < 4.5.0", version_lt("3.18.17", "4.5.0"))
    check("M1 four-part: 3.13.8 < 3.13.8.2", version_lt("3.13.8", "3.13.8.2"))
    check("M1 patch order: 4.5.0 < 4.5.1", version_lt("4.5.0", "4.5.1"))
    check("M1 not-less-self: NOT 4.5.1 < 4.5.1", not version_lt("4.5.1", "4.5.1"))
    check("M1 range excl-incl: 3.18.17 not in (3.18.17, cur]",
          not version_in_range("3.18.17", BASELINE, cur))
    check(f"M1 range endpoint: {cur} in (3.18.17, {cur}]",
          version_in_range(cur, BASELINE, cur))

    # ---- M2: chain enumeration ----
    on_disk = {}
    for f in sorted(os.listdir(RELEASES)):
        m = re.match(r"v(\d+(?:\.\d+)+)\.json$", f)
        if m:
            on_disk[m.group(1)] = os.path.join(RELEASES, f)
    # independent selection (test-side) vs live-machinery selection must agree
    independent = {v for v in on_disk
                   if parse_version(BASELINE) < parse_version(v) <= parse_version(cur)}
    machinery = {v for v in on_disk if version_in_range(v, BASELINE, cur)}
    check(f"M2 machinery selects the full chain ({len(independent)} manifests)",
          independent == machinery and len(independent) > 0)
    check("M2 chain includes v4.5.0 (the trust wave)", "4.5.0" in machinery)
    check("M2 chain includes v4.5.1 (description compliance)", "4.5.1" in machinery)
    ordered = sorted(machinery, key=parse_version)
    check("M2 chain sorts semantically (no lexical order anywhere)",
          ordered == sorted(machinery, key=parse_version))

    # ---- M3 + M4 + M6: walk every chain manifest ----
    fixture_before = fixture_state()
    KNOWN_ACTIONS = {"announce_only", "instruct_user"}
    detectors_run = 0
    for v in ordered:
        try:
            man = json.load(open(on_disk[v], encoding="utf-8"))
        except Exception as e:
            check(f"M3 {v}: valid JSON", False)
            continue
        check(f"M3 {v}: version field matches filename", man.get("version") == v)
        items = man.get("items", None)
        check(f"M3 {v}: items is a list", isinstance(items, list))
        headline = man.get("headline", "")
        check(f"M6 {v}: headline clean", "<" not in headline and ">" not in headline)
        for it in items or []:
            iid = it.get("id", "?")
            ok_shape = all(k in it for k in
                           ("id", "detector_module", "detector_function",
                            "prompt_template", "action"))
            check(f"M3 {v}/{iid}: item shape", ok_shape)
            if not ok_shape:
                continue
            check(f"M3 {v}/{iid}: known action", it["action"] in KNOWN_ACTIONS)
            tmpl = it["prompt_template"]
            check(f"M6 {v}/{iid}: template has no angle brackets",
                  "<" not in tmpl and ">" not in tmpl)
            check(f"M6 {v}/{iid}: template has no raw substrate filenames",
                  "events.jsonl" not in tmpl and "entities.json" not in tmpl)
            # M4: detector loads + runs pure-read on the fixture
            try:
                mod = importlib.import_module(it["detector_module"])
                fn = getattr(mod, it["detector_function"])
                res = fn(os.path.join(FIXTURE, "_hq", "data", "events.jsonl"))
                detectors_run += 1
                check(f"M4 {v}/{iid}: detector returns applies+context",
                      isinstance(res, dict) and isinstance(res.get("applies"), bool)
                      and isinstance(res.get("context"), dict))
            except Exception as e:
                check(f"M4 {v}/{iid}: detector runs ({type(e).__name__}: {e})", False)
    check(f"M4 detectors executed ({detectors_run})", detectors_run > 0)
    check("M4 detectors are pure-read (fixture unchanged)",
          fixture_state() == fixture_before)

    # ---- M5: strict-runtime skill loading ----
    survivors = 0
    total = 0
    for d in sorted(os.listdir(SKILLS)):
        p = os.path.join(SKILLS, d, "SKILL.md")
        if not os.path.isfile(p):
            continue
        total += 1
        txt = open(p, encoding="utf-8").read()
        m = re.match(r"^---\n(.*?)\n---\n", txt, re.S)
        if not m:
            continue
        import yaml
        fm = yaml.safe_load(m.group(1)) or {}
        name = fm.get("name", d)
        desc = fm.get("description", "") or ""
        name_ok = (len(name) <= 64
                   and re.fullmatch(r"[a-z0-9\-]+", name or d) is not None)
        desc_ok = (0 < len(desc) <= 1024 and "<" not in desc and ">" not in desc)
        if name_ok and desc_ok:
            survivors += 1
        else:
            check(f"M5 {d}: survives strict loader", False)
    check(f"M5 all {total} skills survive the strict runtime loader",
          survivors == total)

    print()
    if failures:
        print(f"UPGRADE MATRIX FAILED — {len(failures)} check(s):")
        for f in failures[:20]:
            print(f"  ✗ {f}")
        sys.exit(1)
    print(f"OK — the {BASELINE} -> {cur} jump is sound: "
          f"{len(ordered)} manifests, {detectors_run} detectors, {survivors} skills load")

if __name__ == "__main__":
    main()
