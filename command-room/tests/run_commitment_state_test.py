#!/usr/bin/env python3
"""Phase 2 Stage A acceptance test — the commitment_state projector + the one
counting API (Build Guide 2026-07-01 §4 Phase 2 Stage A).

Pins three contracts:

1. **The commitment_updated fold.** `commitment_updated` events were
   write-only (written by the orchestrator's `push to [date]` verb and the CRU
   schedule-shift path, read by nothing), so a deferred commitment rendered
   overdue forever off its immutable original `data.due`. The loader now folds
   the latest `data.new_due` into the effective due, read-side only.

2. **One counting API.** `count_commitments` / `commitment_counts` is the only
   place open/overdue/undated/by-direction counts come from.
   `compute_brief_state(...)["counts"]` delegates to it, so brief header ==
   coach headline == commitment_counts() by construction (Bug #85 class,
   permanently).

3. **All counting surfaces call it** (source-side gates): MASTER_TRACKER
   renderer, coach, morning brief, the Commitments orchestrator,
   COMMITMENT_AGING spec — plus brief_state.py stays a working compat shim.

Fixtures use real event shapes (canonical COMMITMENT_SCHEMA envelope + the
live variant shapes cru_match's alias chain exists for).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.join(HERE, "..")
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

import brief_state  # noqa: E402 — the compat shim
import commitment_state  # noqa: E402
from commitment_state import (  # noqa: E402
    commitment_counts,
    compute_brief_state,
    count_commitments,
    load_open_commitments,
)
from cru_match import _commitment_field  # noqa: E402

USER = "person_user"
NOW = "2026-07-02T08:00:00Z"

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


def write_events(events):
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    return path


def main():
    print("=== commitment_state projector + one counting API (Phase 2 Stage A) ===\n")

    # ------------------------------------------------------------------
    print("[1] commitment_updated fold — deferred items stop rendering overdue")
    # ------------------------------------------------------------------
    events = [
        # Canonical shape, due already in the past at NOW.
        {"seq": 1, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "t1",
         "data": {"id": "cmt_A", "title": "Send Sam the deck",
                  "owner_id": USER, "due": "2026-06-25", "status": "open"}},
        # The orchestrator `push to [date]` shape: data.commitment_id + new_due.
        {"seq": 2, "ts": "2026-06-26T09:00:00Z", "type": "commitment_updated",
         "source_skill": "cr-commitments", "primary_thread_id": "t1",
         "data": {"commitment_id": "cmt_A", "new_due": "2026-07-10",
                  "reason": "user push"}},
    ]
    path = write_events(events)
    opens = load_open_commitments(path)
    os.unlink(path)
    check("deferred commitment still open", len(opens) == 1, f"{len(opens)} open")
    eff_due = _commitment_field(opens[0], "due") if opens else None
    check("effective due is the pushed date", eff_due == "2026-07-10",
          f"due={eff_due!r} — the write-only original 2026-06-25 must not win")
    check("fold records provenance (due_updated_by_seq)",
          opens and (opens[0].get("data") or {}).get("due_updated_by_seq") == 2)
    counts = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    check("deferred item is NOT stuck/overdue", counts["stuck"] == 0,
          f"stuck={counts['stuck']} — deferral must clear overdue")
    brief = compute_brief_state(open_commitments=opens, user_person_id=USER, now_iso=NOW)
    na = brief["needs_attention"]
    check("brief surfaces the effective due, not overdue",
          len(na) == 1 and na[0]["due"] == "2026-07-10" and na[0]["overdue"] is False,
          f"needs_attention={na}")

    # ------------------------------------------------------------------
    print("\n[2] fold semantics — latest wins; summary-only updates don't erase; unknown ids safe")
    # ------------------------------------------------------------------
    events = [
        {"seq": 1, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
         "primary_thread_id": "t1",
         "data": {"id": "cmt_B", "title": "Draft the SOW", "owner_id": USER,
                  "due": "2026-06-22", "status": "open"}},
        {"seq": 2, "ts": "2026-06-23T10:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_B", "new_due": "2026-06-28"}},
        # CRU schedule-shift shape: change_summary only, NO due field — must
        # not erase the seq-2 deferral.
        {"seq": 3, "ts": "2026-06-24T10:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_B",
                  "change_summary": "scope narrowed to phase 1", "evidence": "email"}},
        # A later push — latest due-carrying update wins.
        {"seq": 4, "ts": "2026-06-29T10:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_B", "new_due": "2026-07-20"}},  # DATE_GUARD_OK: due-fold data; count/brief paths take now_iso=NOW
        # Update for an id that matches nothing — ignored, never a crash.
        {"seq": 5, "ts": "2026-06-29T11:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_GHOST", "new_due": "2026-08-01"}},  # DATE_GUARD_OK: due-fold data for a ghost id; no clock comparison
    ]
    path = write_events(events)
    opens = load_open_commitments(path)
    os.unlink(path)
    eff_due = _commitment_field(opens[0], "due") if opens else None
    check("latest due-carrying update wins", eff_due == "2026-07-20", f"due={eff_due!r}")  # DATE_GUARD_OK: asserts the fold value, not a clock-derived status
    check("summary-only update did not erase the deferral chain",
          opens and (opens[0].get("data") or {}).get("due_updated_by_seq") == 4)
    check("unknown-target update ignored safely", len(opens) == 1)

    # ------------------------------------------------------------------
    print("\n[3] fold on variant shapes + closure still wins over deferral")
    # ------------------------------------------------------------------
    events = [
        # cr-past-meetings variant: due_date + state + owner_person_id.
        {"seq": 1, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
         "primary_thread_id": "t2",
         "data": {"id": "cmt_C", "title": "Send variant recap",
                  "owner_person_id": USER, "due_date": "2026-06-21", "state": "open"}},
        {"seq": 2, "ts": "2026-06-22T10:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_C", "new_due": "2026-07-11"}},
        # A deferred-then-resolved commitment must NOT come back open.
        {"seq": 3, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
         "primary_thread_id": "t3",
         "data": {"id": "cmt_D", "title": "Reply to the investor",
                  "owner_id": USER, "due": "2026-06-21", "status": "open"}},
        {"seq": 4, "ts": "2026-06-22T10:00:00Z", "type": "commitment_updated",
         "data": {"commitment_id": "cmt_D", "new_due": "2026-07-11"}},
        {"seq": 5, "ts": "2026-06-23T10:00:00Z", "type": "commitment_resolved",
         "data": {"commitment_id": "cmt_D", "resolved_by": USER, "evidence": "sent"}},
    ]
    path = write_events(events)
    opens = load_open_commitments(path)
    os.unlink(path)
    ids = {(_commitment_field(o, "title") or "") for o in opens}
    check("variant-shape (due_date) commitment gets the effective due",
          len(opens) == 1 and _commitment_field(opens[0], "due") == "2026-07-11",
          f"opens={[(o.get('data') or {}) for o in opens]}")
    check("resolution still closes a deferred commitment",
          "Reply to the investor" not in ids)

    # ------------------------------------------------------------------
    print("\n[4] count_commitments — the canonical math (direction / stuck / undated / by_kind)")
    # ------------------------------------------------------------------
    def commitment(seq, owner, title, due=None, kind=None):
        data = {"id": f"c{seq}", "title": title, "status": "open"}
        if owner is not None:
            data["owner_id"] = owner
        if due:
            data["due"] = due
        if kind:
            data["kind"] = kind
        return {"seq": seq, "type": "commitment", "primary_thread_id": f"t{seq}",
                "data": data}

    events = [
        commitment(1, USER, "Send Bob the deck", due="2026-07-30"),  # DATE_GUARD_OK: due carried as data; counts take now_iso=NOW
        commitment(2, USER, "Draft the SOW", due="2026-06-01"),          # overdue → stuck
        commitment(3, USER, "Reply to the investor update"),             # undated
        commitment(4, "person_bob", "Bob returns the signed contract", kind="task"),  # undated
        commitment(5, "person_amy", "Amy sends the vendor quote", due="2026-07-05"),
        commitment(6, None, "Bob to provide a BAA"),                     # unowned, undated
        commitment(7, None, "Jake to download Granola", due="2026-06-15"),  # unowned, stuck
    ]
    path = write_events(events)
    opens = load_open_commitments(path)
    c = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    check("total == len(open set) == 7", c["total"] == len(opens) == 7, f"{c}")
    check("direction split (you 3 / they 2 / unowned 2)",
          c["you_owe"] == 3 and c["they_owe"] == 2 and c["unowned"] == 2, f"{c}")
    check("total == you + they + unowned", c["total"] == c["you_owe"] + c["they_owe"] + c["unowned"])
    check("stuck counts overdue only (2)", c["stuck"] == 2, f"{c}")
    check("undated counts no-parseable-due (3)", c["undated"] == 3, f"{c}")
    check("by_kind defaults missing kind to promise (6 promise / 1 task)",
          c["by_kind"] == {"promise": 6, "task": 1}, f"{c['by_kind']}")

    # Parity: the brief's counts block IS the counting API's dict.
    brief = compute_brief_state(open_commitments=opens, user_person_id=USER, now_iso=NOW)
    check("compute_brief_state counts == count_commitments (same code path)",
          brief["counts"] == c, f"brief={brief['counts']} api={c}")

    # Unresolvable user degrades safely — totals stay exact.
    c_nouser = count_commitments(opens, user_person_id=None, now_iso=NOW)
    check("user_person_id=None keeps total/stuck/undated exact",
          c_nouser["total"] == 7 and c_nouser["stuck"] == 2 and c_nouser["undated"] == 3
          and c_nouser["you_owe"] == 0 and c_nouser["they_owe"] == 5, f"{c_nouser}")
    os.unlink(path)

    # ------------------------------------------------------------------
    print("\n[5] commitment_counts(workspace_root) — the I/O wrapper resolves the primary user")
    # ------------------------------------------------------------------
    with tempfile.TemporaryDirectory() as ws:
        data_dir = Path(ws) / "_hq" / "data"
        data_dir.mkdir(parents=True)
        (data_dir / "entities.json").write_text(json.dumps({
            "workspace": {"user_person_id": USER},
            "people": [{"id": USER, "canonical_name": "Test User"}],
        }), encoding="utf-8")
        with open(data_dir / "events.jsonl", "w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev) + "\n")
        wc = commitment_counts(ws, now_iso=NOW)
        # v4.6.0 MC2: the wrapper self-derives the movement map, so its
        # headline additionally carries the real stuck/blocked keys. The pure
        # call above passed no movement — compare on the shared keys and
        # assert the MC2 keys exist (their VALUES are covered by
        # run_commitment_activity_test.py).
        wc_headline = dict(wc["headline"])
        has_mc2_keys = ("stuck" in wc_headline and "blocked" in wc_headline)
        wc_headline.pop("stuck", None)
        wc_headline.pop("blocked", None)
        wc_cmp = {**wc, "headline": wc_headline}
        check("workspace-level counts match the pure math", wc_cmp == c,
              f"workspace={wc_cmp} pure={c}")
        check("wrapper headline carries the MC2 stuck/blocked keys",
              has_mc2_keys, wc["headline"])

    # ------------------------------------------------------------------
    print("\n[5b] SUB1 D2 — top-level partition + additive keys")
    # ------------------------------------------------------------------
    events = [
        {"seq": 1, "ts": "2026-06-20T10:00:00Z", "type": "commitment",
         "source_skill": "meeting-notes", "primary_thread_id": "tP",
         "data": {"id": "cmt_PAR", "title": "Prepare the board pack",
                  "kind": "promise", "owner_id": USER, "status": "open"}},
    ] + [
        {"seq": 1 + i, "ts": "2026-06-20T10:05:00Z", "type": "commitment",
         "source_skill": "commitment-triage", "primary_thread_id": "tP",
         "data": {"id": f"cmt_KID{i}", "title": f"Step {i}",
                  "kind": "promise", "owner_id": USER, "status": "open",
                  "parent_id": "cmt_PAR", "parent_seq": 1}}
        for i in (1, 2, 3)
    ]
    path = write_events(events)
    opens = load_open_commitments(path)
    os.unlink(path)
    counts = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    h = counts["headline"]
    check("a parent with 3 open sub-items counts as 1, not 4",
          counts["total"] == 1 and h["total"] == 1, counts["total"])
    check("invariant holds over the top-level partition",
          h["you_owe"] + h["owed_to_you"] + h["unowned"] + h["unconfirmed"]
          == h["total"])
    check("additive keys present when sub-items exist",
          h.get("subitems_open") == 3
          and h.get("subitems_done_of_open_parents") == 0)
    check("brief needs_attention never surfaces a child",
          {r["commitment_id"] for r in compute_brief_state(
              open_commitments=opens, user_person_id=USER,
              now_iso=NOW)["needs_attention"]} == {"cmt_PAR"})
    # zero-subitems workspace: output byte-identical to pre-SUB1 (absent keys)
    path = write_events(events[:1])
    opens = load_open_commitments(path)
    os.unlink(path)
    h0 = count_commitments(opens, user_person_id=USER, now_iso=NOW)["headline"]
    check("no sub-items → additive keys ABSENT (vacuous safety)",
          "subitems_open" not in h0
          and "subitems_done_of_open_parents" not in h0)

    # ------------------------------------------------------------------
    print("\n[6] brief_state.py stays a working compat shim")
    # ------------------------------------------------------------------
    check("brief_state.compute_brief_state IS commitment_state's",
          brief_state.compute_brief_state is commitment_state.compute_brief_state)
    check("brief_state re-exports the counting API",
          getattr(brief_state, "commitment_counts", None) is commitment_counts
          and getattr(brief_state, "count_commitments", None) is count_commitments)
    check("brief_state re-exports the audit wrapper + reader",
          brief_state.compute_and_log_brief_state is commitment_state.compute_and_log_brief_state
          and brief_state.latest_brief_state_event is commitment_state.latest_brief_state_event)

    # ------------------------------------------------------------------
    print("\n[7] source-side gates — every counting surface names the one API")
    # ------------------------------------------------------------------
    def read(rel):
        return open(os.path.join(PLUGIN_ROOT, rel), encoding="utf-8").read()

    tracker_py = read("shared/scripts/render_master_tracker.py")
    check("MASTER_TRACKER renderer counts via count_commitments",
          "count_commitments" in tracker_py and 'count_commitments(open_commitments)["total"]' in tracker_py,
          "the tracker headline must be the canonical total, not the filtered row count")

    coach = read("skills/command-room-coach/SKILL.md")
    check("coach names the counting API (commitment_counts / count_commitments)",
          "commitment_counts" in coach or "count_commitments" in coach,
          "the coach headline gate must anchor on the one counting API")

    brief_md = read("skills/morning-briefing/SKILL.md")
    check("morning brief points at commitment_state (the promoted module)",
          "commitment_state" in brief_md and "compute_brief_state" in brief_md)

    orch_commit = read("skills/enable-command-room-schedules/references/orchestrator-commitments.md")
    check("Commitments orchestrator header counts come from the counting API",
          "count_commitments" in orch_commit or "commitment_counts" in orch_commit,
          "n_total / n_you_owe / n_owed_to_you must come from commitment_state")

    orch_brief = read("skills/enable-command-room-schedules/references/orchestrator-morning-brief.md")
    check("morning-brief orchestrator names compute_brief_state for its counts",
          "compute_brief_state" in orch_brief)

    view_gen = read("references/VIEW_GENERATION.md")
    check("COMMITMENT_AGING spec anchors on the projector + counting API",
          "commitment_state" in view_gen and
          ("commitment_counts" in view_gen or "count_commitments" in view_gen))

    lint_py = read("shared/scripts/writer_contract_lint.py")
    check("writer-contract lint knows commitment_state as a routing helper",
          '"commitment_state"' in lint_py)

    print(f"\n=== Summary: {passed} passed, {failed} failed ===\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
