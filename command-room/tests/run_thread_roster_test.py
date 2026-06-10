#!/usr/bin/env python3
"""Tests for event_refs.py + thread_roster.py (brain-substrate-drift build).

Covers the two failure modes the audit cared about:
  - dual-layer extraction (top-level vs nested `data`) — the C5 ~10x undercount;
  - lineage-aware roster — a member tagged only to a pre-split umbrella must
    surface as `inherited`, not be dropped (the Wetsels case), and overrides
    (pin/suppress) must win.

stdlib only.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))

import event_refs  # noqa: E402
from thread_roster import derive_roster  # noqa: E402

results = {"pass": 0, "fail": 0}


def check(name, cond):
    if cond:
        results["pass"] += 1
        print(f"PASS {name}")
    else:
        results["fail"] += 1
        print(f"FAIL {name}")


# ---- event_refs dual-layer ----
def test_threads_dual_layer():
    check("threads top-level", event_refs.threads_of({"primary_thread_id": "project_009"}) == {"project_009"})
    check("threads nested data.project_id", event_refs.threads_of({"data": {"project_id": "project_009"}}) == {"project_009"})
    check("threads related list", event_refs.threads_of({"related_thread_ids": ["project_1", "project_2"]}) == {"project_1", "project_2"})
    check("threads merge both layers",
          event_refs.threads_of({"primary_thread_id": "project_1", "data": {"project_id": "project_2"}}) == {"project_1", "project_2"})


def test_persons_dual_layer():
    check("persons top-level list", event_refs.persons_of({"person_ids": ["person_1"]}) == {"person_1"})
    check("persons nested owner", event_refs.persons_of({"data": {"owner_person_id": "person_2"}}) == {"person_2"})
    check("persons nested attendees", event_refs.persons_of({"data": {"attendee_person_ids": ["person_3", "person_4"]}}) == {"person_3", "person_4"})
    check("persons owner_id person-prefixed kept", event_refs.persons_of({"data": {"owner_id": "person_5"}}) == {"person_5"})
    check("persons non-person owner_id dropped", event_refs.persons_of({"data": {"owner_id": "org_9"}}) == set())


# ---- thread_roster lineage + overrides ----
def _ws(threads, events):
    ws = Path(tempfile.mkdtemp(prefix="cr-roster-test-"))
    d = ws / "_hq" / "data"
    d.mkdir(parents=True)
    people = [{"id": f"person_{i}", "canonical_name": f"P{i}"} for i in range(1, 7)]
    (d / "entities.json").write_text(json.dumps({"people": people, "threads": threads}), encoding="utf-8")
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    return ws


def test_lineage_inherited_not_dropped():
    threads = [
        {"id": "project_t1", "canonical_name": "Umbrella", "status": "archived"},
        {"id": "project_t2", "canonical_name": "Successor", "status": "active",
         "parent_thread_id": "project_t1", "spawned_from_thread_id": "project_t1"},
    ]
    events = [
        # direct on successor: person_1 twice (high), person_2 once (low)
        {"seq": 1, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
        {"seq": 2, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1", "person_2"]},
        # umbrella-only: person_3 (the "Wetsel" case — must surface as inherited)
        {"seq": 3, "ts": "2026-04-01", "data": {"project_id": "project_t1", "person_ids": ["person_3"]}},
    ]
    r = derive_roster(_ws(threads, events), "project_t2")
    by = {x["person_id"]: x for x in r}
    check("direct high", by["person_1"]["confidence"] == "high")
    check("direct low", by["person_2"]["confidence"] == "low")
    check("umbrella member surfaces as inherited", by.get("person_3", {}).get("confidence") == "inherited")
    check("inherited member NOT dropped", "person_3" in by)


def test_overrides():
    threads = [
        {"id": "project_t2", "canonical_name": "Solo", "status": "active",
         "roster_overrides": {"pin": ["person_6"], "suppress": ["person_2"]}},
    ]
    events = [
        {"seq": 1, "ts": "2026-05-01", "primary_thread_id": "project_t2", "person_ids": ["person_1", "person_2"]},
        {"seq": 2, "ts": "2026-05-02", "primary_thread_id": "project_t2", "person_ids": ["person_1"]},
    ]
    r = derive_roster(_ws(threads, events), "project_t2")
    ids = {x["person_id"]: x for x in r}
    check("suppress removes person", "person_2" not in ids)
    check("pin adds eventless person", ids.get("person_6", {}).get("source") == "pinned")
    check("pin survives with zero events", ids.get("person_6", {}).get("n_events") == 0)


def main():
    test_threads_dual_layer()
    test_persons_dual_layer()
    test_lineage_inherited_not_dropped()
    test_overrides()
    print(f"=== {results['pass']} passed, {results['fail']} failed ===")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
