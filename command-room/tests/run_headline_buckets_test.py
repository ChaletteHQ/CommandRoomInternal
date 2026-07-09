#!/usr/bin/env python3
"""Bucket-consistency test — the one bucket export (v4.5.2 R4; F-47 P2b / F-56).

The dogfood produced FOUR different open-commitment counts in one day
(127 / 63 / 136 / 153) because each surface derived its own buckets: the
daily Commitments chat folded unowned into owed-to-you (52 = 30 + 18 + 4)
while commitment-triage split them (40 + 18). This suite locks the fix:

  1. `count_commitments(...)["headline"]` is THE bucket export:
     you_owe / owed_to_you / unowned / unconfirmed (+ overdue, total).
     Partition invariant: the four buckets sum to total, exactly.
  2. pending_review items are the `unconfirmed` line and are EXCLUDED from
     you_owe / owed_to_you / unowned (the W4b design).
  3. All three surface code paths — morning brief (compute_brief_state),
     the Commitments chat and triage (count_commitments over the loader's
     open set), and the commitment_counts(workspace) wrapper — return
     IDENTICAL headline numbers from the same substrate.
  4. Prose contract scan: the three surface docs consume counts["headline"],
     the owed-to-you fold is gone, and the false "stuck = no movement 21d /
     blocked on a person" caption (R1b — a metric computed nowhere) is gone
     from every SKILL/orchestrator file.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PLUGIN_ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(PLUGIN_ROOT, "shared", "scripts"))

from commitment_state import (  # noqa: E402
    commitment_counts,
    compute_brief_state,
    count_commitments,
)
from cru_match import load_open_commitments  # noqa: E402

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


USER = "person_user"
NOW = "2026-07-08T18:00:00Z"


def _commitment(seq, cid, *, owner=None, due=None, pending=False, kind="promise"):
    data = {"id": cid, "title": f"item {cid}", "status": "open", "kind": kind}
    if owner is not None:
        data["owner_id"] = owner
    if due is not None:
        data["due"] = due
    if pending:
        data["pending_review"] = True
    return {"seq": seq, "ts": "2026-07-01T10:00:00Z", "type": "commitment",
            "source_skill": "meeting-notes", "data": data}


def _fixture_events():
    """Mirrors the real F-56 substrate shapes: confirmed + pending items in
    every ownership class, dated-overdue, dated-future, undated, a closure,
    and a deferral that un-overdues an item (the Stage A fold)."""
    return [
        # Confirmed, user-owed: one overdue, one future-dated, one undated.
        _commitment(1, "cmt_a", owner=USER, due="2026-05-22"),           # overdue
        _commitment(2, "cmt_b", owner=USER, due="2026-08-01"),
        _commitment(3, "cmt_c", owner=USER),
        # Confirmed, counterparty-owed: one overdue, one undated.
        _commitment(4, "cmt_d", owner="person_pedro", due="2026-06-01"),  # overdue
        _commitment(5, "cmt_e", owner="person_michele"),
        # Confirmed, unowned (extraction gap).
        _commitment(6, "cmt_f"),
        # pending_review in EVERY ownership class — all land in unconfirmed.
        _commitment(7, "cmt_g", owner=USER, pending=True),
        _commitment(8, "cmt_h", owner="person_jason", pending=True),
        _commitment(9, "cmt_i", pending=True),
        # A custom-string id (the commit_navid_… class) — still bucket-counted.
        _commitment(10, "commit_navid_2026-05-19_1", owner="person_navid",
                    due="2026-05-19"),                                    # overdue
        # Closed item — must not appear anywhere.
        _commitment(11, "cmt_z", owner=USER, due="2026-05-01"),
        {"seq": 12, "ts": "2026-07-02T10:00:00Z", "type": "commitment_resolved",
         "source_skill": "apply-choices",
         "data": {"commitment_id": "cmt_z", "resolved_by": USER,
                  "evidence": "done", "resolution": "done"}},
        # Deferral: cmt_d pushed to the future — no longer overdue (Stage A fold).
        {"seq": 13, "ts": "2026-07-03T10:00:00Z", "type": "commitment_updated",
         "source_skill": "apply-choices",
         "data": {"commitment_id": "cmt_d", "new_due": "2026-08-15"}},
    ]


def _build_ws(events):
    root = tempfile.mkdtemp(prefix="cr-headline-")
    data_dir = os.path.join(root, "_hq", "data")
    os.makedirs(data_dir)
    with open(os.path.join(data_dir, "events.jsonl"), "w", encoding="utf-8") as f:
        for ev in events:
            f.write(json.dumps(ev) + "\n")
    # Minimal entities.json so resolve_primary_user finds the fixture user.
    with open(os.path.join(data_dir, "entities.json"), "w", encoding="utf-8") as f:
        json.dump({"persons": [{"id": USER, "is_primary_user": True,
                                "canonical_name": "Fixture User"}]}, f)
    return root


def test_headline_partition_and_pending_exclusion():
    print("\n[1] headline partition invariant + pending_review exclusion")
    root = _build_ws(_fixture_events())
    events_path = os.path.join(root, "_hq", "data", "events.jsonl")
    opens = load_open_commitments(events_path)
    check("fixture opens = 10 (11 captured, 1 closed)", len(opens) == 10, len(opens))

    counts = count_commitments(opens, user_person_id=USER, now_iso=NOW)
    h = counts["headline"]
    check("headline.total == counts.total == len(opens)",
          h["total"] == counts["total"] == 10, h)
    check("four buckets partition the open set (sum == total)",
          h["you_owe"] + h["owed_to_you"] + h["unowned"] + h["unconfirmed"] == h["total"], h)
    check("you_owe excludes pending_review (3 confirmed user-owed)",
          h["you_owe"] == 3, h)
    check("owed_to_you excludes pending_review (3 confirmed counterparty-owed)",
          h["owed_to_you"] == 3, h)
    check("unowned excludes pending_review (1 confirmed ownerless)",
          h["unowned"] == 1, h)
    check("unconfirmed == all pending_review items across ownership classes (3)",
          h["unconfirmed"] == 3, h)
    check("overdue respects the deferral fold (cmt_a + navid; cmt_d deferred out)",
          h["overdue"] == 2, h)
    check("legacy you_owe/they_owe/unowned still count pending items (back-compat)",
          counts["you_owe"] == 4 and counts["they_owe"] == 4 and counts["unowned"] == 2,
          counts)
    check("legacy stuck key still present and equals overdue (deprecated alias)",
          counts["stuck"] == counts["overdue"] == 2, counts)
    shutil.rmtree(root, ignore_errors=True)


def test_three_surfaces_identical():
    print("\n[2] the three surface code paths render IDENTICAL headline numbers")
    root = _build_ws(_fixture_events())
    events_path = os.path.join(root, "_hq", "data", "events.jsonl")
    opens = load_open_commitments(events_path)

    # v4.6.0 MC2: every surface passes the SAME movement map (the wrapper
    # derives its own — same function, same file, same result), so the
    # headline comparison covers the stuck/blocked keys too.
    from commitment_activity import derive_commitment_movement
    movement = derive_commitment_movement(events_path)

    # Surface 1 — morning brief: compute_brief_state (its counts delegate;
    # in production compute_and_log_brief_state supplies the movement map).
    brief = compute_brief_state(
        open_commitments=opens, user_person_id=USER, now_iso=NOW,
        commitment_movement=movement,
    )["counts"]["headline"]
    # Surface 2 — the daily Commitments chat / commitment-triage: the
    # count_commitments call their orchestrator prose mandates.
    chat = count_commitments(opens, user_person_id=USER, now_iso=NOW,
                             movement=movement)["headline"]
    # Surface 3 — the I/O wrapper any other consumer uses (self-derives).
    wrapper = commitment_counts(root, user_person_id=USER, now_iso=NOW)["headline"]

    check("brief == commitments chat/triage", brief == chat, (brief, chat))
    check("brief == workspace wrapper", brief == wrapper, (brief, wrapper))
    check("headline carries the real stuck/blocked keys (v4.6.0 MC2)",
          "stuck" in chat and "blocked" in chat, chat)
    check("no surface folds unowned into owed_to_you (the F-47 P2b fold)",
          chat["owed_to_you"] == 3 and chat["unowned"] == 1, chat)
    shutil.rmtree(root, ignore_errors=True)


SURFACE_DOCS = [
    "skills/morning-briefing/SKILL.md",
    "skills/enable-command-room-schedules/references/orchestrator-morning-brief.md",
    "skills/enable-command-room-schedules/references/orchestrator-commitments.md",
    "skills/commitment-triage/SKILL.md",
]

ALL_PROSE_DIRS = ["skills", "shared"]


def test_prose_contract():
    print("\n[3] prose contract: surfaces consume counts[\"headline\"]; the fold "
          "and the false stuck caption are gone")
    for rel in SURFACE_DOCS:
        p = os.path.join(PLUGIN_ROOT, rel)
        text = open(p, encoding="utf-8").read()
        check(f"{rel} references the headline export", '["headline"]' in text, rel)
        check(f"{rel} does not fold unowned into a direction",
              'counts["they_owe"] + counts["unowned"]' not in text, rel)

    # R1b banned the "no movement in 21+ days" caption while the metric was
    # computed nowhere. v4.6.0 MC2 computes it for real (commitment_activity.
    # classify_commitments), so the caption is legitimate again — but ONLY
    # where the text also names the real derivation. A file using the caption
    # without referencing MC2 / commitment_activity / headline["stuck"] is
    # re-attaching the old lie to some other number.
    caption_hits = []
    for d in ALL_PROSE_DIRS:
        for dirpath, _dirs, files in os.walk(os.path.join(PLUGIN_ROOT, d)):
            for fn in files:
                if not fn.endswith((".md", ".py")):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    text = open(fp, encoding="utf-8").read()
                except OSError:
                    continue
                if ("no movement in 21+ days" in text
                        and "commitment_activity" not in text
                        and "MC2" not in text):
                    caption_hits.append(os.path.relpath(fp, PLUGIN_ROOT))
    check("the stuck caption appears only alongside the real MC2 derivation",
          caption_hits == [], caption_hits)


if __name__ == "__main__":
    test_headline_partition_and_pending_exclusion()
    test_three_surfaces_identical()
    test_prose_contract()
    print(f"\n=== Summary: {passed} passed, {failed} failed ===")
    sys.exit(1 if failed else 0)
