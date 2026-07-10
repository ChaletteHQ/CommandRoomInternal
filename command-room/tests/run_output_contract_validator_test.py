#!/usr/bin/env python3
"""
Tests for `shared/scripts/output_contract_validator.py` (SPEC B3).

Covers the contract the validator must satisfy:

  - Per-kind RULES_BY_KIND floors/caps match the five composer SKILL.mds.
  - validate_brief raises OutputContractError on BLOCKING violations; the
    non-raising twin collect_contract_violations returns every violation.
  - CLIENT SAFETY: the call_prep total-word floor is WARN severity (it never
    blocks a save); its fix_hint points at the contract='report' escape valve.
  - The two explicitly-allowed placeholder forms pass; all generic placeholder
    patterns fail everywhere else.
  - brief_writer integration: a blocking violation under contract='enforce'
    writes NO file; contract='report'/'off' let the save proceed; JSON
    passthrough honors the contract kwarg.
  - Diagnostics are machine-readable and str(e) names every failing section.
  - Sync guard: set(RULES_BY_KIND) <= set(brief_writer.EYEBROW_BY_KIND).

House convention: non-zero exit = fail.
"""
import os
import sys
import json
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared", "scripts"))

from output_contract_validator import (
    validate_brief,
    collect_contract_violations,
    OutputContractError,
    RULES_BY_KIND,
)


results = {"pass": 0, "fail": 0, "failures": []}


def check(name, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  PASS  {name}")
    else:
        results["fail"] += 1
        results["failures"].append(f"{name} ({detail})" if detail else name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


def words(n):
    return " ".join(["word"] * n)


def has(violations, *, rule=None, section_substr=None):
    """True if any violation matches the rule and/or section substring."""
    for v in violations:
        if rule is not None and v["rule"] != rule:
            continue
        if section_substr is not None and (
            not v.get("section") or section_substr.lower() not in v["section"].lower()
        ):
            continue
        return True
    return False


def get(violations, rule):
    return [v for v in violations if v["rule"] == rule]


# ============================================================================
# call_prep
# ============================================================================
print("\n=== call_prep ===")

# 400-word brief → total_words violation (warn), 800-1500 in the diagnostic,
# report-escape in fix_hint.
v = collect_contract_violations(
    "call_prep", "Acme sync", "Today",
    [{"heading": "Meeting Details", "body": words(400)}],
)
tw = get(v, "total_words")
check("call_prep 400 words → total_words violation", len(tw) == 1)
check("call_prep total_words names 800-1500",
      bool(tw) and "800-1500" in tw[0]["expected"], detail=str(tw))
check("call_prep total_words fix_hint points at contract='report'",
      bool(tw) and "contract='report'" in tw[0]["fix_hint"], detail=str(tw))
check("call_prep total_words is WARN severity (client safety)",
      bool(tw) and tw[0]["severity"] == "warn")

# validate_brief must NOT raise on a warn-only floor — the save proceeds.
try:
    warns = validate_brief(
        "call_prep", "Acme sync", "Today",
        [{"heading": "Meeting Details", "body": words(400)}],
    )
    check("call_prep word floor does not raise (warn-only)", True)
    check("validate_brief returns the warn", has(warns, rule="total_words"))
except OutputContractError as e:
    check("call_prep word floor does not raise (warn-only)", False, str(e))

# 1000-word brief → clean.
v = collect_contract_violations(
    "call_prep", "Acme sync", "Today",
    [{"heading": "Meeting Details", "body": words(1000)}],
)
check("call_prep 1000 words → clean", v == [], detail=str(v))

# Talking Points with 2 bullets → bullet_range violation; 5 → clean.
v = collect_contract_violations(
    "call_prep", "Acme sync", "Today",
    [{"heading": "Meeting Details", "body": words(900)},
     {"heading": "Talking Points", "bullets": ["one", "two"]}],
)
check("call_prep Talking Points 2 bullets → violation",
      has(v, rule="bullet_range", section_substr="Talking Points"))
v = collect_contract_violations(
    "call_prep", "Acme sync", "Today",
    [{"heading": "Meeting Details", "body": words(900)},
     {"heading": "Talking Points", "bullets": ["1", "2", "3", "4", "5"]}],
)
check("call_prep Talking Points 5 bullets → no bullet_range violation",
      not has(v, rule="bullet_range", section_substr="Talking Points"))

# Body containing "TBD" → placeholder violation naming the section.
v = collect_contract_violations(
    "call_prep", "Acme sync", "Today",
    [{"heading": "Meeting Details", "body": "Meeting at 2pm. Location TBD for now."}],
)
check("call_prep 'TBD' → placeholder violation naming the section",
      has(v, rule="placeholder", section_substr="Meeting Details"))

# Internal profile: 600-word brief passes where external fails.
internal_sections = [{"heading": "Meeting Details", "body": words(600)}]
v_ext = collect_contract_violations("call_prep", "1:1", "Today", internal_sections)
v_int = collect_contract_violations(
    "call_prep", "1:1", "Today", internal_sections, profile="call_prep_internal"
)
check("call_prep external 600 words → total_words violation",
      has(v_ext, rule="total_words"))
check("call_prep_internal 600 words → no total_words violation",
      not has(v_int, rule="total_words"), detail=str(v_int))


# ============================================================================
# memo
# ============================================================================
print("\n=== memo ===")

# 180-word paragraph → max_paragraph_words violation (total kept in-band).
v = collect_contract_violations(
    "memo", "Pricing", "Internal",
    [{"heading": "Body", "body": words(180) + "\n\n" + words(100)}],
)
check("memo 180-word paragraph → max_paragraph_words violation",
      has(v, rule="max_paragraph_words"))

# 1100-word memo (paragraphs each < 150) → total cap violation.
big = "\n\n".join(words(100) for _ in range(11))
v = collect_contract_violations("memo", "Strategy", "Internal",
                                [{"heading": "Body", "body": big}])
tw = get(v, "total_words")
check("memo 1100 words → total cap violation", len(tw) == 1, detail=str(v))
check("memo total cap has no max_paragraph_words (paragraphs are < 150)",
      not has(v, rule="max_paragraph_words"))


# ============================================================================
# one_pager
# ============================================================================
print("\n=== one_pager ===")

# 30-word headline (the title) → headline_max_words violation.
v = collect_contract_violations(
    "one_pager", words(30), "Why now",
    [{"heading": "Key Points", "body": words(150)}],
)
check("one_pager 30-word headline → violation", has(v, rule="headline_max_words"))

# Allowed placeholder form → no placeholder violation.
v = collect_contract_violations(
    "one_pager", "Margin fell 17%", "Why now",
    [{"heading": "Supporting Data",
      "bullets": ["62% from 3 suppliers", "[Figure needed — confirm before sending]",
                  "P&L Oct 3"]}],
)
check("one_pager allowed [Figure needed — confirm before sending] → no placeholder violation",
      not has(v, rule="placeholder"), detail=str(v))

# Bare [PLACEHOLDER] → placeholder violation.
v = collect_contract_violations(
    "one_pager", "Margin fell 17%", "Why now",
    [{"heading": "Supporting Data", "bullets": ["62%", "[PLACEHOLDER]", "Oct 3"]}],
)
check("one_pager bare [PLACEHOLDER] → placeholder violation",
      has(v, rule="placeholder"))

# Supporting Data with 7 bullets → bullet_range violation.
v = collect_contract_violations(
    "one_pager", "Margin fell 17%", "Why now",
    [{"heading": "Supporting Data", "bullets": [str(i) for i in range(7)]}],
)
check("one_pager Supporting Data 7 bullets → bullet_range violation",
      has(v, rule="bullet_range", section_substr="Supporting Data"))


# ============================================================================
# decision_memo
# ============================================================================
print("\n=== decision_memo ===")

_dm_base = [
    {"heading": "Framing", "body": "We must decide the timing of the first sales hire."},
    {"heading": "Options", "bullets": ["A. Hire now", "B. Hire in Q4", "C. Defer"]},
    {"heading": "Criteria & weights",
     "table": {"headers": ["Criterion", "Weight"], "rows": [["Speed", "40"], ["Cost", "60"]]}},
    {"heading": "Comparison",
     "matrix": {"headers_row": ["A", "B"], "headers_col": ["Speed", "Cost"],
                "cells": [["hi", "lo"], ["lo", "hi"]]}},
    {"heading": "Recommendation", "body": "Hire now; speed outweighs cost here."},
]

# Missing Comparison → required-section violation.
v = collect_contract_violations(
    "decision_memo", "Hire timing", "Decide by June",
    [s for s in _dm_base if s["heading"] != "Comparison"],
)
check("decision_memo missing Comparison → required_section violation",
      has(v, rule="required_section", section_substr="Comparison"))

# Matrix with a blank cell (list shape) → violation listing (row, col).
dm_blank_list = [dict(s) for s in _dm_base]
for s in dm_blank_list:
    if s["heading"] == "Comparison":
        s["matrix"] = {"headers_row": ["A", "B"], "headers_col": ["Speed", "Cost"],
                       "cells": [["hi", "lo"], ["", "hi"]]}
v = collect_contract_violations("decision_memo", "Hire timing", "Decide by June",
                                dm_blank_list)
mb = get(v, "matrix_blank_cell")
check("decision_memo blank matrix cell (list) → violation", len(mb) >= 1)
check("decision_memo blank matrix cell names (row 1, col 0)",
      bool(mb) and "row 1" in mb[0]["observed"] and "col 0" in mb[0]["observed"],
      detail=str(mb))

# Matrix with a blank cell (dict shape — missing key (1,0)) → violation.
dm_blank_dict = [dict(s) for s in _dm_base]
for s in dm_blank_dict:
    if s["heading"] == "Comparison":
        s["matrix"] = {"cells": {(0, 0): "hi", (0, 1): "lo", (1, 1): "hi"}}
v = collect_contract_violations("decision_memo", "Hire timing", "Decide by June",
                                dm_blank_dict)
mb = get(v, "matrix_blank_cell")
check("decision_memo blank matrix cell (dict) → violation",
      has([{"observed": x["observed"]} for x in mb], section_substr=None) or len(mb) >= 1)
check("decision_memo dict blank cell names (row 1, col 0)",
      any("row 1" in x["observed"] and "col 0" in x["observed"] for x in mb),
      detail=str(mb))

# Full, well-formed decision_memo → clean.
v = collect_contract_violations("decision_memo", "Hire timing", "Decide by June",
                                _dm_base)
check("decision_memo full + valid → clean", v == [], detail=str(v))


# ============================================================================
# board_pack
# ============================================================================
print("\n=== board_pack ===")

# Executive Summary with 8 bullets → bullet_range violation.
v = collect_contract_violations(
    "board_pack", "Board pack", "May",
    [{"heading": "Executive Summary", "bullets": [str(i) for i in range(8)]}],
)
check("board_pack Exec Summary 8 bullets → bullet_range violation",
      has(v, rule="bullet_range", section_substr="Executive Summary"))

# "[add asks here]" → clean (allowed placeholder).
v = collect_contract_violations(
    "board_pack", "Board pack", "May",
    [{"heading": "Asks", "bullets": ["[add asks here]"]}],
)
check("board_pack [add asks here] → no placeholder violation",
      not has(v, rule="placeholder"), detail=str(v))

# Blank KPI table cell → violation.
v = collect_contract_violations(
    "board_pack", "Board pack", "May",
    [{"heading": "KPIs vs Targets",
      "table": {"headers": ["Metric", "Value"], "rows": [["MRR", "478K"], ["NRR", ""]]}}],
)
check("board_pack blank KPI cell → table_blank_cell violation",
      has(v, rule="table_blank_cell"))


# ============================================================================
# Unknown-ish kind — generic rules only
# ============================================================================
print("\n=== weekly_recap (generic only) ===")

v = collect_contract_violations(
    "weekly_recap", "Recap", "This week",
    [{"heading": "Notes", "body": "Short update. Status TBD."}],
)
check("weekly_recap 'TBD' → placeholder violation", has(v, rule="placeholder"))
check("weekly_recap has no word-count rule", not has(v, rule="total_words"))


# ============================================================================
# Diagnostics shape + str(e)
# ============================================================================
print("\n=== diagnostics ===")

multi = collect_contract_violations(
    "decision_memo", "Hire timing", "Decide",
    [{"heading": "Options", "bullets": ["only one"]}],  # missing 4 required sections
)
check("every violation has non-empty rule/expected/fix_hint",
      all(v["rule"] and v["expected"] and v["fix_hint"] for v in multi),
      detail=str(multi))

try:
    validate_brief(
        "decision_memo", "Hire timing", "Decide",
        [{"heading": "Options", "bullets": ["A only", "B only"]}],  # missing 4 sections
    )
    check("validate_brief raises on blocking violations", False, "no raise")
except OutputContractError as e:
    msg = str(e)
    check("validate_brief raises OutputContractError on blocking", True)
    check("str(e) names every failing section",
          all(s in msg for s in ["Framing", "Criteria & weights", "Comparison",
                                 "Recommendation"]),
          detail=msg)
    check("OutputContractError.violations is a non-empty list",
          isinstance(e.violations, list) and len(e.violations) >= 1)


# ============================================================================
# Sync guard
# ============================================================================
print("\n=== sync guard ===")

from brief_writer import EYEBROW_BY_KIND
check("set(RULES_BY_KIND) <= set(EYEBROW_BY_KIND)",
      set(RULES_BY_KIND) <= set(EYEBROW_BY_KIND),
      detail=str(set(RULES_BY_KIND) - set(EYEBROW_BY_KIND)))


# ============================================================================
# brief_writer integration
# ============================================================================
print("\n=== brief_writer integration ===")

from brief_writer import make_brief, make_brief_from_json

_dm_no_rec = [s for s in _dm_base if s["heading"] != "Recommendation"]

with tempfile.TemporaryDirectory() as tmp:
    # enforce: blocking violation → raises, NO file written.
    out = os.path.join(tmp, "enforce.docx")
    try:
        make_brief(out, brief_kind="decision_memo",
                   title="Hire timing", subtitle="Decide by June",
                   sections=_dm_no_rec)
        check("enforce blocking → raises", False, "no raise")
    except OutputContractError:
        check("enforce blocking → raises OutputContractError", True)
    check("enforce blocking → NO file written", not os.path.exists(out))

    # report: same brief saves anyway.
    out_rep = os.path.join(tmp, "report.docx")
    path = make_brief(out_rep, brief_kind="decision_memo",
                      title="Hire timing", subtitle="Decide by June",
                      exec_header={"verdict": "Hire in June."},  # OUT2 §4 flip
                      sections=_dm_no_rec, contract="report")
    check("report mode → file written despite violation", os.path.isfile(out_rep))

    # off: same brief saves silently.
    out_off = os.path.join(tmp, "off.docx")
    make_brief(out_off, brief_kind="decision_memo",
               title="Hire timing", subtitle="Decide by June",
               exec_header={"verdict": "Hire in June."},  # OUT2 §4 flip
               sections=_dm_no_rec, contract="off")
    check("off mode → file written", os.path.isfile(out_off))

    # JSON passthrough: contract honored.
    out_json = os.path.join(tmp, "json.docx")
    payload = json.dumps({
        "output_path": out_json,
        "brief_kind": "call_prep",
        "title": "Thin prep",
        "subtitle": "Today",
        "exec_header": {"verdict": "Walk out with the date set."},
        "sections": [{"heading": "Meeting Details", "body": words(50)}],
        "contract": "off",
    })
    make_brief_from_json(payload)
    check("JSON passthrough honors contract='off'", os.path.isfile(out_json))


# ============================================================================
# Summary
# ============================================================================
print(f"\n=== {results['pass']} passed, {results['fail']} failed ===")
if results["fail"]:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
