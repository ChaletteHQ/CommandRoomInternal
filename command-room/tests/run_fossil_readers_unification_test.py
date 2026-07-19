#!/usr/bin/env python3
"""Fossil-readers follow-through — the last two hand-rolled recency
derivations (render_master_tracker, list-active/render_tree) migrate onto
thread_activity, and thread_activity itself learns the R7 timestamp
spellings. Pins:

1. thread_activity counts legacy-spelled events (`timestamp`, `date` —
   ×156/×17 on the live substrate at the 2026-07-01 audit). Before this,
   the scan parsed top-level `ts` only, so a thread whose latest activity
   was a legacy-spelled event silently read staler than it is.
2. derive_from_events(ALL_TYPES) — renderer "last touched" semantics:
   every event type counts, confidence floor still applies, default-set
   parity with derive_thread_activity is preserved.
3. MASTER_TRACKER Last Activity honors the settled C3 id rule: an event
   whose related_thread_ids references a thread bumps that thread (the old
   loop matched primary_thread_id only — the F-54 divergence class), and
   the zero-event floor is stamp-then-first_seen like every other surface.
4. list-active render_tree: same id rule, confidence floor now enforced
   (VIEW_GENERATION computed_last_activity rule), zero-event floor chain.

Fixture dates are computed RELATIVE TO TODAY (hardcoded-future-date
gotcha). Placeholder names only.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "list-active"))

from thread_activity import (  # noqa: E402
    ALL_TYPES,
    derive_from_events,
    derive_thread_activity,
)
import render_master_tracker as rmt  # noqa: E402
import render_tree as rt  # noqa: E402

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


def iso(days_ago):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).isoformat()


def d(days_ago):
    return (dt.date.today() - dt.timedelta(days=days_ago)).isoformat()


def _ws(threads, events, orgs=None):
    ws = Path(tempfile.mkdtemp(prefix="cr_fossil_unif_"))
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "entities": {
            "people": [],
            "orgs": orgs if orgs is not None else [
                {"id": "org_001", "canonical_name": "Acme Co",
                 "is_primary_focus": True, "scope": "operating"},
            ],
            "threads": threads,
        }
    }), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return ws


def thread(tid, name, **extra):
    t = {"id": tid, "canonical_name": name, "status": "active",
         "affiliation_id": "org_001"}
    t.update(extra)
    return t


def main():
    print("=== fossil-readers follow-through — recency unification ===\n")

    # --- 1. thread_activity: legacy timestamp spellings count ------------------
    events = [
        {"seq": 1, "timestamp": iso(1), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
        {"seq": 2, "date": d(2), "type": "commitment", "source_skill": "x",
         "primary_thread_id": "project_002", "data": {}},
        {"seq": 3, "ts": iso(40), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
    ]
    ws = _ws([], events)
    out = derive_thread_activity(ws)
    check("legacy `timestamp` spelling counts (was silently dropped)",
          "project_001" in out and out["project_001"].seq == 1,
          out.get("project_001"))
    check("legacy `date` spelling counts",
          "project_002" in out and out["project_002"].seq == 2,
          out.get("project_002"))
    check("canonical `ts` wins over a legacy sibling when newer elsewhere",
          out["project_001"].ts > out["project_002"].ts if "project_002" in out else False)

    # --- 2. derive_from_events(ALL_TYPES) --------------------------------------
    events = [
        {"seq": 4, "ts": iso(3), "type": "thread_updated", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
        {"seq": 5, "ts": iso(1), "type": "pack_run", "source_skill": "pulse",
         "primary_thread_id": "project_001", "data": {}},
        {"seq": 6, "ts": iso(2), "type": "interaction", "source_skill": "x",
         "primary_thread_id": "project_001", "classification_confidence": 0.1,
         "data": {}},
    ]
    all_out = derive_from_events(events, activity_types=ALL_TYPES)
    check("ALL_TYPES counts non-default event types",
          all_out.get("project_001") is not None
          and all_out["project_001"].seq == 5, all_out.get("project_001"))
    check("ALL_TYPES still applies the confidence floor",
          all_out["project_001"].seq != 6)
    check("default set ignores infra types (parity with derive_thread_activity)",
          derive_from_events(events) == {})

    # --- 3. master tracker: settled C3 id rule + zero-event floor ---------------
    threads = [
        thread("project_001", "Alpha engagement", first_seen=d(90)),
        thread("project_002", "Beta engagement", first_seen=d(90)),
        thread("project_003", "Gamma engagement",
               last_activity=d(10), first_seen=d(90)),
    ]
    events = [
        # Alpha: direct activity 30d ago.
        {"seq": 1, "ts": iso(30), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
        # Beta: NO direct events — cross-referenced by another thread's
        # meeting yesterday (primary lives off-table so only the related
        # credit is what bumps Beta).
        {"seq": 2, "ts": iso(1), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_offtable",
         "related_thread_ids": ["project_002"], "data": {}},
        # Gamma: zero events anywhere — must fall to the stored stamp.
    ]
    ws = _ws(threads, events)
    content, _counts = rmt._build_content(ws)
    rows = [ln for ln in content.splitlines() if ln.startswith("|") and "engagement" in ln]
    idx = {name: i for i, ln in enumerate(rows)
           for name in ("Alpha", "Beta", "Gamma") if name in ln}
    check("tracker: related_thread_ids bumps the referenced thread "
          "(Beta sorts above Alpha)",
          idx.get("Beta", 99) < idx.get("Alpha", -1), rows)
    gamma_row = next((ln for ln in rows if "Gamma" in ln), "")
    check("tracker: zero-event floor reads the stored stamp (date passes "
          "through _localize_date unchanged)",
          d(10) in gamma_row, gamma_row)
    check("tracker: fossil never overrides derived activity "
          "(Beta's row shows event-derived recency, not first_seen)",
          d(90) not in next((ln for ln in rows if "Beta" in ln), d(90)))

    # legacy `timestamp` spelling flows through the tracker scan too
    events_legacy = [
        {"seq": 3, "timestamp": iso(1), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
    ]
    ws = _ws([thread("project_001", "Alpha engagement", first_seen=d(90))],
             events_legacy)
    content, _ = rmt._build_content(ws)
    alpha_row = next((ln for ln in content.splitlines()
                      if ln.startswith("|") and "Alpha" in ln), "")
    check("tracker: legacy `timestamp`-spelled event counts (not first_seen)",
          d(90) not in alpha_row and alpha_row, alpha_row)

    # --- 4. render_tree: same rule, conf floor, floor chain ---------------------
    entities = {
        "orgs": [{"id": "org_001", "canonical_name": "Acme Co"}],
        "threads": [
            {"id": "project_001", "canonical_name": "Alpha engagement",
             "status": "active", "affiliation_id": "org_001"},
            {"id": "project_002", "canonical_name": "Beta engagement",
             "status": "active", "affiliation_id": "org_001"},
            {"id": "project_003", "canonical_name": "Gamma engagement",
             "status": "active", "affiliation_id": "org_001",
             "last_activity": d(10), "first_seen": d(90)},
        ],
    }
    events = [
        {"seq": 1, "ts": iso(30), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_001", "data": {}},
        {"seq": 2, "ts": iso(1), "type": "meeting", "source_skill": "x",
         "primary_thread_id": "project_offtable",
         "related_thread_ids": ["project_002"], "data": {}},
        # low-confidence noise on Beta must not be its recency
        {"seq": 3, "ts": iso(0), "type": "interaction", "source_skill": "x",
         "primary_thread_id": "project_002", "classification_confidence": 0.05,
         "data": {}},
    ]
    roots, _ws_projects = rt.build_tree(entities, events, None, False)
    projs = {p.id: p for p in roots[0].projects}
    check("tree: related_thread_ids bumps the referenced project",
          (projs["project_002"].last_activity or "") > (projs["project_001"].last_activity or ""),
          {k: v.last_activity for k, v in projs.items()})
    check("tree: confidence floor enforced (low-conf event is not Beta's recency)",
          (projs["project_002"].last_activity or "").startswith(iso(1)[:10]),
          projs["project_002"].last_activity)
    check("tree: zero-event floor reads stored stamp then first_seen",
          projs["project_003"].last_activity == d(10),
          projs["project_003"].last_activity)
    check("tree: projects sort by derived recency (Beta first)",
          roots[0].projects[0].id == "project_002",
          [p.id for p in roots[0].projects])

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
