#!/usr/bin/env python3
"""
Tests for `shared/scripts/quantify.py` (SPEC EXEC1 element 3).

The load-bearing contract: quantify.py is the ONLY sanctioned source of inline
dollar tags AND it NEVER estimates. The most important test is the negative one
— a thread/commitment with no revenue field returns the time part only (or None),
never a fabricated figure. A client without QuickBooks gets no dollar tag.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "shared", "scripts"))

from quantify import money_time_tag

results = {"pass": 0, "fail": 0, "failures": []}


def check(name, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  PASS  {name}")
    else:
        results["fail"] += 1
        results["failures"].append(f"{name} ({detail})" if detail else name)
        print(f"  FAIL  {name}{(' — ' + detail) if detail else ''}")


NOW = "2026-06-14"

ENTS_WITH_DEAL = {
    "threads": [{"id": "project_017", "affiliation_id": "org_acme"}],
    "orgs": [{"id": "org_acme", "canonical_name": "Acme Co", "deal_value": 40000}],
}
ENTS_NO_MONEY = {
    "threads": [{"id": "project_017", "affiliation_id": "org_x"}],
    "orgs": [{"id": "org_x", "canonical_name": "X"}],  # NO money field
}

COMMITMENT = {"type": "commitment", "primary_thread_id": "project_017",
              "data": {"due": "2026-06-02"}}  # 12 days before NOW


# ============================================================================
# Test 1 — full tag: time + money compose with " · "
# ============================================================================
print("\n=== full money×time tag ===")
tag = money_time_tag(COMMITMENT, ENTS_WITH_DEAL, now=NOW)
check("composes '12d late · $40K deal'", tag == "12d late · $40K deal", f"got {tag!r}")

# ============================================================================
# Test 2 — NO ESTIMATION: missing money field → time part only, never a figure
# ============================================================================
print("\n=== no estimation path (the load-bearing test) ===")
tag = money_time_tag(COMMITMENT, ENTS_NO_MONEY, now=NOW)
check("no money field → time only", tag == "12d late", f"got {tag!r}")
check("no '$' fabricated when field absent", tag is not None and "$" not in tag, f"got {tag!r}")

# thread that resolves to no org at all → still no money fabricated
orphan = {"type": "commitment", "primary_thread_id": "project_999", "data": {"due": "2026-06-02"}}
tag = money_time_tag(orphan, ENTS_WITH_DEAL, now=NOW)
check("unresolvable thread → time only, no money", tag == "12d late", f"got {tag!r}")

# ============================================================================
# Test 3 — nothing derivable → None (not an empty string, not a guess)
# ============================================================================
print("\n=== None when nothing derivable ===")
check("no due + no money → None", money_time_tag({"type": "commitment"}, {}, now=NOW) is None)
check("empty dict entities + no due → None",
      money_time_tag({"primary_thread_id": "project_017"}, ENTS_NO_MONEY, now=NOW) is None)
check("non-dict input → None", money_time_tag("not a dict", ENTS_WITH_DEAL, now=NOW) is None)

# ============================================================================
# Test 4 — money-only tag (not yet overdue, but org has a value)
# ============================================================================
print("\n=== money only (not overdue) ===")
future = {"type": "commitment", "primary_thread_id": "project_017", "data": {"due": "2027-01-01"}}
tag = money_time_tag(future, ENTS_WITH_DEAL, now=NOW)
check("future due → money part only", tag == "$40K deal", f"got {tag!r}")

# ============================================================================
# Test 5 — revenue-style field carries no 'deal' label
# ============================================================================
print("\n=== revenue field label ===")
ents_rev = {
    "threads": [{"id": "project_017", "affiliation_id": "org_r"}],
    "orgs": [{"id": "org_r", "canonical_name": "R", "historical_revenue": 240000}],
}
tag = money_time_tag(COMMITMENT, ents_rev, now=NOW)
check("revenue field → '$240K' (no 'deal' suffix)", tag == "12d late · $240K", f"got {tag!r}")

# ============================================================================
# Test 6 — money formatting (K / M thresholds)
# ============================================================================
print("\n=== money formatting ===")
def _money_only(value):
    ents = {"threads": [{"id": "t", "affiliation_id": "o"}],
            "orgs": [{"id": "o", "deal_value": value}]}
    item = {"primary_thread_id": "t"}  # no due → money only
    return money_time_tag(item, ents, now=NOW)

check("1.2M formats as $1.2M", _money_only(1_200_000) == "$1.2M deal", f"got {_money_only(1_200_000)!r}")
check("1M formats as $1M (no .0)", _money_only(1_000_000) == "$1M deal", f"got {_money_only(1_000_000)!r}")
check("540 formats as $540", _money_only(540) == "$540 deal", f"got {_money_only(540)!r}")
check("string '$40,000' parses", _money_only("$40,000") == "$40K deal", f"got {_money_only('$40,000')!r}")
check("zero/negative → no money part", _money_only(0) is None and _money_only(-5) is None)
check("non-numeric string → no money part", _money_only("a lot") is None)

# ============================================================================
# Test 7 — thread input (last_activity → quiet days)
# ============================================================================
print("\n=== thread input: quiet days ===")
thread = {"id": "project_017", "affiliation_id": "org_acme", "last_activity": "2026-05-26"}
tag = money_time_tag(thread, ENTS_WITH_DEAL, now=NOW)
check("thread with last_activity → 'Nd quiet · $40K deal'",
      tag == "19d quiet · $40K deal", f"got {tag!r}")

# ============================================================================
# Test 8 — value annotated directly on the item (no org needed)
# ============================================================================
print("\n=== value on the item itself ===")
item = {"primary_thread_id": "project_017", "data": {"due": "2026-06-02", "deal_value": 90000}}
tag = money_time_tag(item, {}, now=NOW)
check("item-level deal_value used when no entities", tag == "12d late · $90K deal", f"got {tag!r}")


print(f"\n=== {results['pass']} passed, {results['fail']} failed ===")
if results["fail"]:
    print("Failures:")
    for f in results["failures"]:
        print(f"  - {f}")
    sys.exit(1)
sys.exit(0)
