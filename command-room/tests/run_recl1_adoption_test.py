#!/usr/bin/env python3
"""RECL1 — fleet-wide adoption of thread_activity.apply_reclassifications
(SPEC_RECL1, read-side Pass-8 fold behind the `honor_reclassifications`
opt-in seam).

Pins, per surface (every fence goes RED when the fold is removed at its
site — mutation-verified, see BUILD_RECL1 record):

  seam    derive_thread_activity / derive_from_events (+ org_activity twins)
          honor corrections only when honor_reclassifications=True; the
          DEFAULT path is FROZEN byte-identical on a reclassification-
          BEARING stream (full-dict equality — the §5 freeze fence).
  A1      stall_detector day-counts correct in BOTH directions (borrowed
          activity flags honestly; moved-in activity stops false flags).
  A3      deal_health rot flags follow the corrected activity map.
  A4      build_dcc_input Layer-3 stale fallback keys on corrected activity.
  A5      build_workspace_map_input last-touched follows corrections.
  A6/A7   renderer ALL_TYPES semantics: corrections move the original
          event's credit AND the reclassification event's own ts bumps the
          corrected thread ("last touched" — the kept-in-stream semantic,
          pinned, not fought). render_tree's raw fallback loop UNCHANGED.
  A8      render_org_history: an event moved off an org's thread leaves
          that org's timeline/stats and joins the corrected one's.
  A9      entity_resolve recency ranking flips once a move is honored.

Fixture rules: canonical v2.2 envelopes + one legacy data-level-spelling /
legacy-`timestamp` event (real-data fixture gotcha); dates RELATIVE TO
TODAY, offsets far from the 14/21/30/60/180-day thresholds (G14 +
cron-boundary gotcha); placeholder names only.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "shared" / "scripts"))
sys.path.insert(0, str(ROOT / "skills" / "list-active"))

import org_activity as oa  # noqa: E402
import thread_activity as ta  # noqa: E402

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


NOW = dt.datetime.now(dt.timezone.utc)


def iso(days_ago: int, hour: int, minute: int = 0) -> str:
    """Aware-UTC ISO, midday-UTC hours only (15-18) so LA/UTC localization
    never straddles a date boundary (cron-boundary fixture gotcha)."""
    d = (NOW - dt.timedelta(days=days_ago)).replace(
        hour=hour, minute=minute, second=0, microsecond=0)
    return d.isoformat()


def d(days_ago: int) -> str:
    return (NOW - dt.timedelta(days=days_ago)).date().isoformat()


def _aware(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s)


T_OLD, T_NEW, T_DEAL, T_OTHER = "project_901", "project_902", "project_903", "project_904"
ORG_NEW, ORG_OLD = "org_901", "org_902"

# --- the shared fixture stream (§4) ---------------------------------------
# E1 meeting 5d → T-old; R1 moves it to T-new.
# E2 interaction 40d → T-old, LEGACY: `timestamp` spelling + data.project_id.
# E3 commitment 5d → T-old; R2 dismisses (primary null, related empty).
# E4 meeting 5d conf 0.30 → T-new; R3 confirms at conf 1.0 (floor-lift).
E1_TS, E2_TS, E3_TS, E4_TS = iso(5, 17), iso(40, 15), iso(5, 15), iso(5, 16)
R1_TS, R2_TS, R3_TS = iso(2, 15), iso(2, 15, 5), iso(2, 16)

EVENTS = [
    {"seq": 5, "timestamp": E2_TS, "type": "interaction", "source_skill": "t",
     "data": {"project_id": T_OLD, "summary": "old email"}},
    {"seq": 10, "ts": E1_TS, "type": "meeting", "source_skill": "t",
     "primary_thread_id": T_OLD, "related_thread_ids": [],
     "data": {"title": "kickoff sync"}},
    {"seq": 11, "ts": R1_TS, "type": "reclassification", "source_skill": "t",
     "supersedes_seq": 10, "primary_thread_id": T_NEW,
     "related_thread_ids": [], "classification_confidence": 1.0,
     "data": {"reason": "user moved the meeting"}},
    {"seq": 12, "ts": E3_TS, "type": "commitment", "source_skill": "t",
     "primary_thread_id": T_OLD, "related_thread_ids": [],
     "data": {"title": "send the deck"}},
    {"seq": 13, "ts": R2_TS, "type": "reclassification", "source_skill": "t",
     "supersedes_seq": 12, "primary_thread_id": None,
     "related_thread_ids": [], "classification_confidence": 1.0,
     "data": {"reason": "user dismissed the classification"}},
    {"seq": 14, "ts": E4_TS, "type": "meeting", "source_skill": "t",
     "primary_thread_id": T_NEW, "related_thread_ids": [],
     "classification_confidence": 0.30, "data": {"title": "low-conf sync"}},
    {"seq": 15, "ts": R3_TS, "type": "reclassification", "source_skill": "t",
     "supersedes_seq": 14, "primary_thread_id": T_NEW,
     "related_thread_ids": [], "classification_confidence": 1.0,
     "data": {"reason": "user confirmed the classification"}},
]


def _ws(threads, events, orgs=None, people=None):
    ws = Path(tempfile.mkdtemp())
    data = ws / "_hq" / "data"
    data.mkdir(parents=True)
    (data / "entities.json").write_text(json.dumps({
        "entities": {
            "people": people if people is not None else [],
            "orgs": orgs if orgs is not None else [
                # is_primary_focus → the master tracker renders a full
                # threads table (Last Activity column) for the A6 fence.
                {"id": ORG_NEW, "canonical_name": "Sample Partners",
                 "is_primary_focus": True},
                {"id": ORG_OLD, "canonical_name": "Placeholder Ventures",
                 "is_primary_focus": True},
            ],
            "threads": threads,
        }
    }), encoding="utf-8")
    (data / "events.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
    return ws


def main():
    print("=== RECL1 — reclassification adoption (seam + per-surface fences) ===\n")

    # ------------------------------------------------------------------ seam
    print("-- seam unit: thread_activity --")
    folded = ta.derive_from_events(EVENTS, honor_reclassifications=True)
    check("T-new latest = E1's ts (moved-in activity credits the corrected thread)",
          T_NEW in folded and folded[T_NEW].ts == _aware(E1_TS)
          and folded[T_NEW].seq == 10, folded.get(T_NEW))
    check("T-old latest = E2's ts (E1 moved away, E3 dismissed → honest 40d)",
          T_OLD in folded and folded[T_OLD].ts == _aware(E2_TS)
          and folded[T_OLD].seq == 5, folded.get(T_OLD))
    check("floor-lift: confirmed E4 counts only through the fold (conf 0.30 → 1.0)",
          # E4's patched confidence clears the 0.40 floor; without R3 it is
          # skipped entirely. E1 is newer, so prove E4 via a narrowed stream.
          ta.derive_from_events(
              [EVENTS[5], EVENTS[6]], honor_reclassifications=True
          ).get(T_NEW) is not None
          and ta.derive_from_events([EVENTS[5], EVENTS[6]]) == {})

    # §5 freeze: the DEFAULT path on this reclassification-BEARING stream is
    # byte-identical to the raw derivation — full-dict equality, pinned by hand.
    raw = ta.derive_from_events(EVENTS)
    expected_raw = {
        T_OLD: ta.ThreadActivity(seq=10, event_type="meeting", ts=_aware(E1_TS)),
    }
    check("FREEZE: default path = raw derivation exactly (full dict equality)",
          raw == expected_raw, raw)
    check("FREEZE: kwarg omitted == kwarg False",
          raw == ta.derive_from_events(EVENTS, honor_reclassifications=False))

    # Workspace-level entry point (derive_thread_activity → _iter_events).
    ws = _ws([{"id": T_OLD, "canonical_name": "Thread Alpha", "status": "active"},
              {"id": T_NEW, "canonical_name": "Thread Beta", "status": "active"}],
             EVENTS)
    check("derive_thread_activity threads the kwarg through _iter_events",
          ta.derive_thread_activity(ws, honor_reclassifications=True) == folded)
    check("derive_thread_activity default stays raw",
          ta.derive_thread_activity(ws) == expected_raw)

    # ALL_TYPES renderer semantics (A6/A7 pin — kept-in-stream): the
    # reclassification event's own ts bumps the corrected thread.
    all_folded = ta.derive_from_events(EVENTS, activity_types=ta.ALL_TYPES,
                                       honor_reclassifications=True)
    check("ALL_TYPES + fold: T-new last-touched = R3's ts (correction time counts)",
          all_folded.get(T_NEW) is not None
          and all_folded[T_NEW].ts == _aware(R3_TS)
          and all_folded[T_NEW].seq == 15, all_folded.get(T_NEW))
    check("ALL_TYPES + fold: T-old = E2's ts (everything else moved/dismissed)",
          all_folded.get(T_OLD) is not None
          and all_folded[T_OLD].ts == _aware(E2_TS), all_folded.get(T_OLD))
    all_raw = ta.derive_from_events(EVENTS, activity_types=ta.ALL_TYPES)
    check("FREEZE: ALL_TYPES raw unchanged (E1 credits T-old; R3 bumps T-new)",
          all_raw.get(T_OLD) == ta.ThreadActivity(10, "meeting", _aware(E1_TS))
          and all_raw.get(T_NEW) == ta.ThreadActivity(15, "reclassification",
                                                      _aware(R3_TS)), all_raw)

    # ------------------------------------------------------------ org seam
    print("\n-- seam unit: org_activity --")
    t_org = {T_NEW: ORG_NEW, T_OLD: ORG_OLD}
    day_types = ["meeting", "commitment", "decision", "interaction"]
    org_folded = oa.derive_from_events(EVENTS, thread_org=t_org,
                                       activity_types=day_types,
                                       honor_reclassifications=True)
    check("org fold: E1's move carries its org credit (ORG_NEW = E1's ts)",
          org_folded.get(ORG_NEW) is not None
          and org_folded[ORG_NEW].ts == _aware(E1_TS), org_folded.get(ORG_NEW))
    check("org fold: ORG_OLD reads honestly quiet (E2 only, 40d)",
          org_folded.get(ORG_OLD) is not None
          and org_folded[ORG_OLD].ts == _aware(E2_TS), org_folded.get(ORG_OLD))
    org_raw = oa.derive_from_events(EVENTS, thread_org=t_org,
                                    activity_types=day_types)
    expected_org_raw = {
        ORG_OLD: oa.OrgActivity(seq=10, event_type="meeting", ts=_aware(E1_TS)),
    }
    check("FREEZE: org default path = raw derivation exactly (full dict equality)",
          org_raw == expected_org_raw, org_raw)
    check("derive_org_activity threads the kwarg (workspace entry point)",
          oa.derive_org_activity(
              ws, entities={"threads": [
                  {"id": T_NEW, "affiliation_id": ORG_NEW},
                  {"id": T_OLD, "affiliation_id": ORG_OLD}]},
              activity_types=day_types, honor_reclassifications=True,
          ) == org_folded)

    # ------------------------------------------------------------------- A1
    print("\n-- A1 stall_detector --")
    from stall_detector import detect_stalled_projects
    # T-old borrows E1/E3's 5d activity raw (would NOT flag); honest = 40d → flags.
    # T-new raw has zero events → first_seen 60d floor (WOULD false-flag);
    # honest = 5d moved-in activity → no flag. Both directions, one fixture.
    ws_stall = _ws([
        {"id": T_OLD, "canonical_name": "Thread Alpha", "status": "active",
         "affiliation_id": ORG_OLD},
        {"id": T_NEW, "canonical_name": "Thread Beta", "status": "active",
         "first_seen": d(60), "affiliation_id": ORG_NEW},
    ], EVENTS)
    flags = detect_stalled_projects(ws_stall)
    by_id = {f["thread_id"]: f for f in flags}
    check("borrowed activity flags honestly (T-old stalled ~40d)",
          # 39 or 40 depending on run hour vs the fixture's 15:00Z stamp —
          # keep the assert far from the 14d threshold, not knife-edge exact.
          T_OLD in by_id and 35 <= by_id[T_OLD]["days_since_activity"] <= 41
          and by_id[T_OLD]["baseline_source"] == "event_scan", flags)
    check("moved-in activity stops the false flag (T-new NOT stalled)",
          T_NEW not in by_id, flags)
    raw_map = ta.derive_thread_activity(
        ws_stall, activity_types=set(day_types))
    check("(control) raw derivation would read T-old 5d fresh — the fold is the fence",
          raw_map[T_OLD].ts == _aware(E1_TS))

    # ------------------------------------------------------------------- A3
    print("\n-- A3 deal_health --")
    import deal_health
    # Deal thread's only recent contact (E5, 3d) was reclassified away (R4);
    # honest activity = E6 at 40d > negotiating threshold 7 → rotting.
    deal_events = [
        {"seq": 19, "ts": iso(40, 16), "type": "interaction", "source_skill": "t",
         "primary_thread_id": T_DEAL, "related_thread_ids": [],
         "data": {"summary": "old call"}},
        {"seq": 20, "ts": iso(3, 15), "type": "interaction", "source_skill": "t",
         "primary_thread_id": T_DEAL, "related_thread_ids": [],
         "data": {"summary": "misfiled touch"}},
        {"seq": 21, "ts": iso(2, 15), "type": "reclassification", "source_skill": "t",
         "supersedes_seq": 20, "primary_thread_id": T_OTHER,
         "related_thread_ids": [], "classification_confidence": 1.0,
         "data": {"reason": "user moved the touch"}},
    ]
    ws_deal = _ws([{"id": T_DEAL, "canonical_name": "Deal Thread", "kind": "deal"}],
                  deal_events)
    deal_row = [{"thread_id": T_DEAL, "name": "Deal Thread", "org_id": ORG_NEW,
                 "deal": {"stage": "negotiating", "opened_at": d(50)}}]
    common = dict(open_commitment_thread_ids={T_DEAL}, today=NOW.date())
    with_fold = deal_health.compute_deal_health(
        deal_row, activity_by_thread=ta.derive_thread_activity(
            ws_deal, honor_reclassifications=True), **common)
    without = deal_health.compute_deal_health(
        deal_row, activity_by_thread=ta.derive_thread_activity(ws_deal), **common)
    check("rotting fires on the corrected map (40d quiet in negotiating)",
          with_fold[0]["days_quiet"] == 40 and "rotting" in with_fold[0]["flags"],
          with_fold[0])
    check("(control) raw map hides the rot (3d borrowed touch)",
          without[0]["days_quiet"] == 3 and "rotting" not in without[0]["flags"],
          without[0])

    # ------------------------------------------------------------------- A4
    print("\n-- A4 build_dcc_input Layer 3 --")
    from build_dcc_input import _project_matters_fallback
    matters = _project_matters_fallback(ws_stall, NOW)
    stale_headlines = [m["headline"] for m in matters
                       if m.get("type") == "anomaly"]
    check("Layer-3 stale fallback keys on corrected activity (Thread Alpha quiet)",
          any("Thread Alpha" in h for h in stale_headlines), matters)
    check("Layer-3 does not stale-flag the corrected thread (Thread Beta fresh)",
          not any("Thread Beta" in h for h in stale_headlines), stale_headlines)

    # ------------------------------------------------------------------- A5
    print("\n-- A5 build_workspace_map_input --")
    out = subprocess.run(
        [sys.executable, str(ROOT / "shared" / "scripts" / "build_workspace_map_input.py"),
         "--workspace-root", str(ws_stall)],
        capture_output=True, text=True, encoding="utf-8", timeout=120)
    check("map builder runs", out.returncode == 0, out.stderr[-400:])
    projects = {p["id"]: p for p in json.loads(
        json.loads(out.stdout)["PROJECTS_JSON"])} if out.returncode == 0 else {}
    check("map last-touched: T-old reads ~40d (corrected), not the borrowed 5d",
          projects.get(T_OLD, {}).get("lastH", 0) > 30 * 24, projects.get(T_OLD))
    check("map last-touched: T-new reads fresh (~5d moved-in activity)",
          0 < projects.get(T_NEW, {}).get("lastH", 10**9) < 10 * 24,
          projects.get(T_NEW))

    # ------------------------------------------------------------------- A6
    print("\n-- A6 render_master_tracker --")
    from render_master_tracker import _build_content
    content, _counts = _build_content(ws_stall)
    # Kept-in-stream pin: under ALL_TYPES the correction itself is the
    # "last touched" for T-new (R3's date), and T-old falls back to E2's.
    r3_date, e2_date, e1_date = R3_TS[:10], E2_TS[:10], E1_TS[:10]
    lines = [ln for ln in content.splitlines() if "Thread Beta" in ln]
    check("tracker Last Activity: T-new = the correction's date (kept-in-stream)",
          lines and r3_date in lines[0], lines)
    old_lines = [ln for ln in content.splitlines() if "Thread Alpha" in ln]
    check("tracker Last Activity: T-old = E2's honest date (moved/dismissed gone)",
          old_lines and e2_date in old_lines[0] and e1_date not in old_lines[0],
          old_lines)

    # ------------------------------------------------------------------- A7
    print("\n-- A7 list-active render_tree --")
    import render_tree
    la = render_tree._compute_last_activity(EVENTS)
    check("tree canonical path folds (T-new = R3's ts, T-old = E2's ts)",
          la.get(T_NEW, "").startswith(r3_date)
          and la.get(T_OLD, "").startswith(e2_date), la)
    # Never-brick posture: the raw fallback loop is byte-identical — sabotage
    # the import and pin the OLD loop's exact output (primary_thread_id + ts
    # only; no fold, no type filter, no legacy spellings).
    saved = sys.modules.pop("thread_activity")
    sys.modules["thread_activity"] = None  # "from X import Y" now raises
    try:
        fb = render_tree._compute_last_activity(EVENTS)
    finally:
        sys.modules["thread_activity"] = saved
    expected_fb = {}
    for ev in EVENTS:
        pt, ts = ev.get("primary_thread_id"), ev.get("ts")
        if pt and ts and (pt not in expected_fb or ts > expected_fb[pt]):
            expected_fb[pt] = ts
    check("tree fallback loop UNCHANGED (raw, no fold — never-brick posture)",
          fb == expected_fb, (fb, expected_fb))

    # ------------------------------------------------------------------- A8
    print("\n-- A8 render_org_history --")
    from render_org_history import compile_org_history
    ws_org = _ws([
        {"id": T_OLD, "canonical_name": "Thread Alpha", "affiliation_id": ORG_OLD},
        {"id": T_NEW, "canonical_name": "Thread Beta", "affiliation_id": ORG_NEW},
    ], EVENTS)
    c_new = compile_org_history(ws_org, ORG_NEW)
    c_old = compile_org_history(ws_org, ORG_OLD)
    new_timeline = [t["label"] for t in c_new["timeline"]]
    old_timeline = [t["label"] for t in c_old["timeline"]]
    check("moved meeting's timeline row lands under the corrected org",
          any("kickoff sync" in lbl for lbl in new_timeline), new_timeline)
    check("…and leaves the old org's timeline",
          not any("kickoff sync" in lbl for lbl in old_timeline), old_timeline)
    check("old org's derived stats follow (1 honest event, last_touch = E2)",
          c_old["event_count"] == 1 and c_old["last_touch"] == e2_date,
          (c_old["event_count"], c_old["last_touch"]))

    # ------------------------------------------------------------------- A9
    print("\n-- A9 entity_resolve recency ranking --")
    import entity_resolve as er
    cands = [{"id": T_OLD, "canonical_name": "Thread Alpha"},
             {"id": T_NEW, "canonical_name": "Thread Beta"}]
    ranked = er._sort_by_observed_recency(list(cands), ws_stall)
    check("ranking flips once the move is honored (T-new outranks T-old)",
          [c["id"] for c in ranked] == [T_NEW, T_OLD],
          [c["id"] for c in ranked])
    check("(control) raw recency would rank T-old first (borrowed E1)",
          ta.derive_thread_activity(ws_stall)[T_OLD].ts == _aware(E1_TS)
          and T_NEW not in ta.derive_thread_activity(ws_stall))

    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
